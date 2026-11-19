"""Subscription transport, conversion and management API requests."""
import json
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error

from .settings import ConfigError

DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"


def failure_reason(error):
    """Classify failures without including URLs, headers or remote error bodies."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed"
    if isinstance(reason, ssl.SSLError):
        return "TLS connection failed"
    if isinstance(reason, socket.gaierror):
        return "DNS lookup failed"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "connection timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, ValueError):
        return "invalid URL or request format; check env-file quotes and escaping"
    return "network connection failed"


def urls(settings):
    return [part.strip() for part in re.split(r"[\r\n,;]+", settings.get("CLASH_SUBSCRIPTION_URL")) if part.strip()]


def fetch(url, settings):
    headers = {"User-Agent": settings.get("SUBSCRIPTION_USER_AGENT", DEFAULT_USER_AGENT)}
    extra = settings.get("SUBSCRIPTION_HEADERS")
    if extra:
        name, separator, value = extra.partition(":")
        if not separator:
            raise ConfigError("SUBSCRIPTION_HEADERS must be a Name: value header")
        headers[name.strip()] = value.strip()
    context = ssl._create_unverified_context() if settings.get("ALLOW_INSECURE_TLS").lower() == "true" else ssl.create_default_context()
    failure = "empty response"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers)
            # Keep downloads independent of ambient proxy environment settings.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
            with opener.open(request, timeout=60) as response:
                data = response.read()
            if data:
                return data
            failure = "empty response"
        except (OSError, ValueError) as error:
            failure = failure_reason(error)
        if attempt < 2:
            time.sleep(3)
    raise ConfigError(f"Subscription download failed after retries: {failure}")


def download(settings):
    entries = urls(settings)
    if not entries:
        raise ConfigError("No subscription URL provided")
    if settings.get("SUBCONVERTER_ENABLED").lower() != "true":
        return fetch(entries[0], settings)
    endpoint = settings.get("SUBCONVERTER_URL", "http://localhost:25500").rstrip("/") + "/sub?"
    attempts = ["|".join(entries)] + entries if len(entries) > 1 else entries
    failure = ""
    for entry in attempts:
        try:
            return fetch(endpoint + urllib.parse.urlencode({"target": "clash", "url": entry}), settings)
        except ConfigError as error:
            failure = str(error)
    raise ConfigError(f"Subconverter download failed: {failure}")


def api_request(settings, path, method="GET", payload=None, secret=None):
    headers = {}
    token = settings.get("CLASH_API_SECRET") if secret is None else secret
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(settings.api + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return response.status
