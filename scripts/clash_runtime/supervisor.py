"""Small resident supervisor; configuration and networking run in a worker."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from .settings import ConfigError, cron_schedule


def log(message):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + message, flush=True)


def atomic_write(path, data):
    # Scheduler setup needs no YAML/network imports in the resident process.
    from tempfile import NamedTemporaryFile
    temporary = None
    try:
        with NamedTemporaryFile(dir=Path(path).parent, delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def run(settings):
    children = []
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
            start(["/app/tools/subconverter/subconverter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        initializer = start([sys.executable, str(Path(__file__).resolve().parents[1] / "clash.py"), "initialize"])
        while initializer.poll() is None and not stopping:
            if any(child.poll() is not None for child in children if child is not initializer):
                raise ConfigError("Auxiliary service exited unexpectedly")
            time.sleep(0.2)
        if stopping:
            return 0
        if initializer.returncode:
            raise ConfigError("Configuration initialization failed")
        children.remove(initializer)
        mihomo = start(["/usr/local/bin/clash", "-d", "/root/.config/clash", "-ext-ui", "dashboard"])
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
