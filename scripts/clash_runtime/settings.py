"""Validated environment settings. Never include credentials in errors."""
import os
import json
import re
import unicodedata
from dataclasses import dataclass


class ConfigError(Exception):
    pass


PORTS = {
    "CLASH_HTTP_PORT": ("port", 0),
    "CLASH_SOCKS_PORT": ("socks-port", 0),
    "CLASH_MIXED_PORT": ("mixed-port", 7890),
    "CLASH_EXTERNAL_CONTROLLER_PORT": ("external-controller", 9090),
}


@dataclass
class Settings:
    env: dict
    ports: dict
    authentication: list | None = None

    @classmethod
    def load(cls, env=None):
        env = dict(os.environ if env is None else env)
        ports, used = {}, {}
        for name, (key, default) in PORTS.items():
            raw = env.get(name) or str(default)
            if not re.fullmatch(r"[0-9]+", raw) or len(raw.lstrip("0")) > 5:
                raise ConfigError(f"{name}: expected a decimal port")
            port = int(raw.lstrip("0") or "0")
            minimum = 1 if key == "external-controller" else 0
            if not minimum <= port <= 65535:
                raise ConfigError(f"{name}: port outside {minimum}..65535")
            if port and port in used:
                raise ConfigError(f"{name}: conflicts with {used[port]}")
            if port:
                used[port] = name
            ports[key] = port
        user, password = env.get("CLASH_PROXY_USERNAME", ""), env.get("CLASH_PROXY_PASSWORD", "")
        raw_users = env.get("CLASH_PROXY_USERS_JSON", "")
        if raw_users and (user or password):
            raise ConfigError("CLASH_PROXY_USERS_JSON cannot be combined with single-user credentials")
        if bool(user) != bool(password):
            raise ConfigError("CLASH_PROXY_USERNAME and CLASH_PROXY_PASSWORD must both be set")
        authentication = None
        if raw_users:
            try:
                users = json.loads(raw_users)
            except (ValueError, RecursionError):
                raise ConfigError("CLASH_PROXY_USERS_JSON must be valid JSON") from None
            if not isinstance(users, list) or not users:
                raise ConfigError("CLASH_PROXY_USERS_JSON must be a non-empty array")
            authentication, seen = [], set()
            for index, entry in enumerate(users, 1):
                if not isinstance(entry, dict) or set(entry) != {"username", "password"}:
                    raise ConfigError(f"CLASH_PROXY_USERS_JSON entry {index}: expected username and password fields")
                name, secret = entry["username"], entry["password"]
                validate_credentials(name, secret)
                if name in seen:
                    raise ConfigError(f"CLASH_PROXY_USERS_JSON entry {index}: duplicate username")
                seen.add(name)
                authentication.append(name + ":" + secret)
        elif user:
            validate_credentials(user, password)
            authentication = [user + ":" + password]
        header = env.get("SUBSCRIPTION_HEADERS", "")
        if header and (":" not in header or not header.partition(":")[0].strip() or "\n" in header or "\r" in header):
            raise ConfigError("SUBSCRIPTION_HEADERS must be a single Name: value header")
        return cls(env, ports, authentication)

    def get(self, key, default=""):
        return self.env.get(key) or default

    @property
    def api(self):
        return self.get("CLASH_API", f"http://127.0.0.1:{self.ports['external-controller']}").rstrip("/")


def validate_credentials(user, password):
    if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
        raise ConfigError("Proxy username and password must be non-empty strings")
    if ":" in user or any(unicodedata.category(c) == "Cc" for c in user + password):
        raise ConfigError("Proxy credentials contain forbidden characters")


def cron_schedule(settings):
    if settings.get("CLASH_CRON_TEST") == "1":
        return "* * * * *"
    hour = settings.get("CLASH_UPDATE_HOUR", "3")
    if not re.fullmatch(r"[0-9]{1,2}", hour) or not 0 <= int(hour) <= 23:
        raise ConfigError("CLASH_UPDATE_HOUR must be 0..23")
    interval = settings.get("CLASH_UPDATE_INTERVAL")
    if interval:
        if re.fullmatch(r"[0-9]{1,2}", interval) and 1 <= int(interval) <= 23:
            return "0 * * * *" if int(interval) == 1 else f"0 */{int(interval)} * * *"
        # Preserve the previous fallback for invalid intervals.
    return f"0 {int(hour)} * * *"
