"""YAML transformations and atomic, private configuration files."""
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from .settings import ConfigError


def transform(raw, settings):
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise ConfigError("Invalid YAML configuration") from None
    if not isinstance(config, dict):
        raise ConfigError("Configuration root must be a mapping")
    for key in ("port", "socks-port", "mixed-port"):
        if settings.ports[key]:
            config[key] = settings.ports[key]
        else:
            config.pop(key, None)
    config["external-controller"] = f"0.0.0.0:{settings.ports['external-controller']}"
    config["allow-lan"] = True
    if settings.get("CLASH_API_SECRET"):
        config["secret"] = settings.get("CLASH_API_SECRET")
    if settings.authentication is not None:
        config["authentication"] = list(settings.authentication)
        config["skip-auth-prefixes"] = []
    if settings.get("DISABLE_CLASH_DNS").lower() in ("true", "1", "yes"):
        if isinstance(config.get("dns"), dict):
            config["dns"]["enable"] = False
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".clash-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate(data, directory):
    fd, candidate = tempfile.mkstemp(prefix=".candidate-", suffix=".yaml", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        result = subprocess.run(
            ["/usr/local/bin/clash", "-t", "-d", str(directory), "-f", candidate],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
        )
        if result.returncode:
            raise ConfigError("Mihomo rejected candidate configuration")
    except subprocess.TimeoutExpired:
        raise ConfigError("Mihomo configuration validation timed out") from None
    finally:
        os.unlink(candidate)
