"""Offline Docker integration checks. Run after building clash-test:local."""
import json
from pathlib import Path
import subprocess
import tempfile
import time


def docker(*args, check=True):
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def request(proxy, credentials=None, socks=False):
    command = ["curl", "--silent", "--show-error", "--max-time", "10", "--noproxy", "", "--proxy",
               ("socks5h://" if socks else "http://") + proxy]
    if credentials:
        command += ["--proxy-user", credentials]
    command += ["--output", "/dev/null", "--write-out", "%{http_code}", "http://target:8000/"]
    result = docker("exec", "clash-smoke", *command, check=False)
    return result.returncode, result.stdout


def assert_http_status(proxy, expected, credentials=None):
    code, status = request(proxy, credentials)
    assert (code, status) == (0, expected), (
        f"HTTP proxy check ({'credentials supplied' if credentials else 'no credentials'}): "
        f"expected curl=0 HTTP={expected}, got curl={code} HTTP={status!r}"
    )


def wait_ready():
    for _ in range(90):
        if docker("exec", "clash-smoke", "python3", "/app/scripts/clash.py", "healthcheck", check=False).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("Container readiness timed out")


def main():
    docker("network", "create", "clash-smoke-net")
    try:
        # Same image supplies an isolated local HTTP target and subscription server.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.joinpath("config.yaml").write_text("rules:\n  - MATCH,DIRECT\n")
            docker("run", "-d", "--name", "clash-target", "--network", "clash-smoke-net", "--network-alias", "target",
                   "-v", f"{root.resolve()}:/www:ro", "--entrypoint", "python3", "clash-test:local", "-m", "http.server", "8000", "--directory", "/www")
            for mixed, controller in ((7890, 9090), (17890, 19090)):
                multi_user = mixed == 17890
                auth_args = (["-e", "CLASH_PROXY_USERS_JSON=" + json.dumps([
                    {"username": "user", "password": "password"},
                    {"username": "second", "password": "another:password"},
                ])] if multi_user else ["-e", "CLASH_PROXY_USERNAME=user", "-e", "CLASH_PROXY_PASSWORD=password"])
                docker("run", "-d", "--name", "clash-smoke", "--network", "clash-smoke-net",
                       "-e", f"CLASH_MIXED_PORT={mixed}", "-e", f"CLASH_EXTERNAL_CONTROLLER_PORT={controller}",
                       *auth_args,
                       "-e", "CLASH_API_SECRET=management", "-e", "CLASH_SUBSCRIPTION_URL=http://target:8000/config.yaml",
                       "clash-test:local")
                try:
                    wait_ready()
                    # Initialization must exit before steady state. PID 1 keeps
                    # only the small supervisor, without YAML/network imports.
                    processes = docker("top", "clash-smoke", "-eo", "args").stdout
                    assert "clash.py initialize" not in processes, processes
                    print(docker("exec", "clash-smoke", "sh", "-c",
                                 "grep -E 'VmRSS|VmHWM' /proc/1/status").stdout)
                    proxy = f"127.0.0.1:{mixed}"
                    assert_http_status(proxy, "407")
                    assert_http_status(proxy, "403", "user:wrong")
                    assert request(proxy, "user:password") == (0, "200")
                    assert request(proxy, "user:wrong", socks=True)[0] != 0
                    assert request(proxy, "user:password", socks=True) == (0, "200")
                    if multi_user:
                        assert request(proxy, "second:another:password") == (0, "200")
                        assert request(proxy, "second:another:password", socks=True) == (0, "200")
                        assert_http_status(proxy, "403", "second:password")
                    docker("exec", "clash-smoke", "python3", "/app/scripts/clash.py", "update")
                    assert_http_status(proxy, "407")
                    assert request(proxy, "user:password", socks=True) == (0, "200")
                    if multi_user:
                        assert request(proxy, "second:another:password", socks=True) == (0, "200")
                    # Invalid remote configuration must leave current listeners usable.
                    root.joinpath("config.yaml").write_text("[]\n")
                    assert docker("exec", "clash-smoke", "python3", "/app/scripts/clash.py", "update", check=False).returncode != 0
                    assert request(proxy, "user:password") == (0, "200")
                    root.joinpath("config.yaml").write_text("rules:\n  - MATCH,DIRECT\n")
                    docker("stop", "--time", "12", "clash-smoke")
                    state = json.loads(docker("inspect", "clash-smoke").stdout)[0]["State"]
                    assert state["ExitCode"] == 0, state
                finally:
                    print(docker("logs", "clash-smoke", check=False).stdout)
                    docker("rm", "-f", "clash-smoke", check=False)
            invalid = docker("run", "--rm", "-e", "CLASH_HTTP_PORT=bad", "clash-test:local", check=False)
            assert invalid.returncode != 0 and "CLASH_HTTP_PORT" in invalid.stdout
    finally:
        docker("rm", "-f", "clash-target", check=False)
        docker("network", "rm", "clash-smoke-net", check=False)


if __name__ == "__main__":
    main()
