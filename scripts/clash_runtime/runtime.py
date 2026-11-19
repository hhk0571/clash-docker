"""Transactional updates and PID 1 supervision."""
import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path
import urllib.request
import yaml
from .configuration import atomic_write, transform, validate
from .settings import ConfigError, cron_schedule
from .subscription import api_request, download, fetch


def log(message):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + message, flush=True)


def secret_from(data):
    return (yaml.safe_load(data) or {}).get("secret", "") or ""


class Runtime:
    def __init__(self, settings, directory="/root/.config/clash", mounted="/app/config", example="/config/config.yaml.example"):
        self.settings = settings
        self.directory = Path(directory)
        self.target = self.directory / "config.yaml"
        self.mounted = Path(mounted)
        self.backup = self.mounted / "config.yaml"
        self.example = Path(example)

    @contextlib.contextmanager
    def lock(self):
        import fcntl
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".update.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConfigError("Another configuration update is running") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def prepare(self, raw):
        data = transform(raw, self.settings)
        validate(data, self.directory)
        return data

    def sync(self, data):
        try:
            atomic_write(self.backup, data)
        except OSError:
            log("WARNING: mounted configuration backup could not be saved")

    def reload(self, secret):
        status = api_request(self.settings, "/configs?force=true", "PUT", {"path": str(self.target)}, secret)
        if status not in (200, 204):
            raise ConfigError("Configuration reload rejected")

    def update(self):
        with self.lock():
            data = self.prepare(download(self.settings))
            old = self.target.read_bytes()
            atomic_write(self.target, data)
            try:
                self.reload(secret_from(old))
            except Exception:
                atomic_write(self.target, old)
                restored = False
                for secret in dict.fromkeys([secret_from(old), secret_from(data)]):
                    try:
                        self.reload(secret)
                        restored = True
                        break
                    except Exception:
                        pass
                log("Reload failed; old configuration restored" + (" and reloaded" if restored else "; recovery reload failed"))
                raise ConfigError("Configuration update failed") from None
            self.sync(data)
            log("Configuration updated and reloaded")

    def initialize(self):
        with self.lock():
            data = None
            if self.settings.get("CLASH_SUBSCRIPTION_URL"):
                try:
                    data = self.prepare(download(self.settings))
                except (ConfigError, OSError) as error:
                    reason = str(error) if isinstance(error, ConfigError) else "configuration file operation failed"
                    log(f"Subscription unavailable or invalid ({reason}); trying local configuration")
            if data is None:
                for path in (self.backup, self.example):
                    if path.is_file():
                        try:
                            data = self.prepare(path.read_bytes())
                            break
                        except (ConfigError, OSError):
                            log("Local configuration candidate rejected")
            if data is None:
                raise ConfigError("No valid startup configuration available")
            atomic_write(self.target, data)
            self.sync(data)

    def geo_files(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        base = self.settings.get("GEO_DAT_URL", "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest").rstrip("/")
        for name in ("geoip.metadb", "geosite.dat"):
            mounted, target = self.mounted / name, self.directory / name
            if mounted.is_file():
                atomic_write(target, mounted.read_bytes())
            elif not target.is_file():
                try:
                    atomic_write(target, fetch(base + "/" + name, self.settings))
                except ConfigError:
                    log(f"WARNING: {name} unavailable; Mihomo may download it")


def healthcheck(settings):
    secret = settings.get("CLASH_API_SECRET")
    if not secret:
        try:
            secret = secret_from(Path("/root/.config/clash/config.yaml").read_bytes())
        except (OSError, yaml.YAMLError):
            pass
    if api_request(settings, "/version", secret=secret) != 200:
        raise ConfigError("Mihomo healthcheck failed")


def run(settings):
    runtime, children = Runtime(settings), []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)

    def start(command, new_session=True, **kwargs):
        if stopping:
            raise ConfigError("Startup interrupted")
        # dcron calls setpgid itself, which fails for a session leader. Give it
        # a dedicated process group without setsid; retain group-based shutdown.
        child = subprocess.Popen(command, start_new_session=new_session,
                                 **({} if new_session else {"process_group": 0}), **kwargs)
        children.append(child)
        return child

    try:
        schedule = cron_schedule(settings) if settings.get("CLASH_SUBSCRIPTION_URL") else None
        if settings.get("SUBCONVERTER_ENABLED").lower() == "true":
            # Subconverter may log full subscription URLs; do not forward its output.
            converter = start(["/app/tools/subconverter/subconverter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            endpoint = settings.get("SUBCONVERTER_URL", "http://localhost:25500").rstrip("/") + "/version"
            for _ in range(30):
                if stopping or converter.poll() is not None:
                    raise ConfigError("Subconverter startup failed")
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(endpoint, timeout=1):
                        break
                except OSError:
                    time.sleep(1)
            else:
                raise ConfigError("Subconverter readiness timed out")
        runtime.geo_files()
        runtime.initialize()
        mihomo = start(["/usr/local/bin/clash", "-d", str(runtime.directory), "-ext-ui", "dashboard"])
        if schedule:
            command = f"{schedule} /usr/bin/python3 /app/scripts/clash.py update >/proc/1/fd/1 2>&1\n"
            atomic_write("/etc/crontabs/root", command.encode())
            start(["crond", "-f"], new_session=False)
            log("Subscription scheduler started: " + schedule)
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None:
                    if child is mihomo:
                        return code if code >= 0 else 128 - code
                    raise ConfigError("Auxiliary service exited unexpectedly")
            time.sleep(0.2)
        return 0
    finally:
        stop(None, None)
        deadline = time.monotonic() + 8
        for child in children:
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
