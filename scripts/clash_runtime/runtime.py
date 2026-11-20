"""Short-lived initialization, transactional updates and health checks."""
import contextlib
import os
import time
from pathlib import Path
import urllib.request
import yaml
from .configuration import atomic_write, transform, validate
from .settings import ConfigError
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


def initialize(settings):
    if settings.get("SUBCONVERTER_ENABLED").lower() == "true":
        endpoint = settings.get("SUBCONVERTER_URL", "http://localhost:25500").rstrip("/") + "/version"
        for _ in range(30):
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(endpoint, timeout=1):
                    break
            except OSError:
                time.sleep(1)
        else:
            raise ConfigError("Subconverter readiness timed out")
    runtime = Runtime(settings)
    runtime.geo_files()
    runtime.initialize()
