import contextlib
import json
import os
import signal
import socket
import ssl
import subprocess
import urllib.error
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from clash_runtime.settings import Settings, ConfigError, cron_schedule
from clash_runtime.configuration import transform, validate
from clash_runtime.runtime import Runtime, healthcheck
from clash_runtime.supervisor import run
from clash_runtime.subscription import download, fetch, api_request, failure_reason, DEFAULT_USER_AGENT


class SettingsTests(unittest.TestCase):
    def test_multi_user_authentication(self):
        password = 'p:a"\\$中文'
        raw = json.dumps([{"username": "alice", "password": password}, {"username": "bob", "password": "b"}])
        settings = Settings.load({"CLASH_PROXY_USERS_JSON": raw})
        config = yaml.safe_load(transform(b"authentication: [old]\nskip-auth-prefixes: [127.0.0.0/8]", settings))
        self.assertEqual(config["authentication"], ["alice:" + password, "bob:b"])
        self.assertEqual(config["skip-auth-prefixes"], [])
        self.assertIsNone(Settings.load({"CLASH_PROXY_USERS_JSON": ""}).authentication)

    def test_invalid_multi_user_authentication(self):
        cases = ["not-json", "[]", "null", "{}", '"text"',
                 '[{"username":"a"}]', '[{"username":"a","password":"p","extra":1}]',
                 '[{"username":1,"password":"p"}]', '[{"username":"a","password":false}]',
                 '[{"username":"","password":"p"}]', '[{"username":"a","password":""}]',
                 '[{"username":"a:b","password":"p"}]',
                 json.dumps([{"username": "a", "password": "secret\n"}]),
                 '[{"username":"a","password":"p"},{"username":"a","password":"q"}]']
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ConfigError) as error:
                Settings.load({"CLASH_PROXY_USERS_JSON": value})
            self.assertNotIn("secret", str(error.exception))
        valid = '[{"username":"a","password":"p"}]'
        for single in ({"CLASH_PROXY_USERNAME": "a"}, {"CLASH_PROXY_PASSWORD": "p"}, {"CLASH_PROXY_USERNAME": "a", "CLASH_PROXY_PASSWORD": "p"}):
            with self.assertRaisesRegex(ConfigError, "cannot be combined"):
                Settings.load({"CLASH_PROXY_USERS_JSON": valid, **single})

    def test_defaults(self):
        self.assertEqual(Settings.load({}).ports, {"port": 0, "socks-port": 0, "mixed-port": 7890, "external-controller": 9090})

    def test_invalid_ports(self):
        for value in ("-1", "+1", "1.0", " 80", "80 ", "localhost:80", "65536", "１２", "9" * 5000):
            with self.subTest(value=value[:20]), self.assertRaises(ConfigError):
                Settings.load({"CLASH_HTTP_PORT": value})
        with self.assertRaises(ConfigError):
            Settings.load({"CLASH_EXTERNAL_CONTROLLER_PORT": "0"})
        with self.assertRaises(ConfigError):
            Settings.load({"CLASH_HTTP_PORT": "7890"})

    def test_boundaries(self):
        self.assertEqual(Settings.load({"CLASH_HTTP_PORT": "00001", "CLASH_SOCKS_PORT": "65535"}).ports["port"], 1)
        self.assertEqual(Settings.load({"CLASH_HTTP_PORT": "0" * 5000}).ports["port"], 0)

    def test_credentials(self):
        for env in ({"CLASH_PROXY_USERNAME": "a"}, {"CLASH_PROXY_PASSWORD": "p"},
                    {"CLASH_PROXY_USERNAME": "a:b", "CLASH_PROXY_PASSWORD": "p"},
                    {"CLASH_PROXY_USERNAME": "a", "CLASH_PROXY_PASSWORD": "p\n"}):
            with self.assertRaises(ConfigError):
                Settings.load(env)

    def test_transform(self):
        password = "a:'\\#:@"
        settings = Settings.load({"CLASH_PROXY_USERNAME": "u", "CLASH_PROXY_PASSWORD": password,
                                  "CLASH_API_SECRET": "a'\\b", "DISABLE_CLASH_DNS": "true"})
        config = yaml.safe_load(transform(b"custom: {a: 1}\ndns: {enable: true}\nauthentication: [old]\nskip-auth-prefixes: [127.0.0.0/8]", settings))
        self.assertEqual(config["authentication"], ["u:" + password])
        self.assertEqual(config["skip-auth-prefixes"], [])
        self.assertEqual(config["custom"], {"a": 1})
        self.assertFalse(config["dns"]["enable"])
        self.assertEqual(config["secret"], "a'\\b")
        original = yaml.safe_load(transform(b"authentication: [old]\nskip-auth-prefixes: [127.0.0.0/8]", Settings.load({})))
        self.assertEqual(original["authentication"], ["old"])
        self.assertEqual(original["skip-auth-prefixes"], ["127.0.0.0/8"])

    def test_bad_yaml(self):
        for raw in (b"[]", b"null", b"a: ["):
            with self.assertRaises(ConfigError):
                transform(raw, Settings.load({}))

    def test_disabled_proxy_ports_are_omitted(self):
        for raw in (b"rules: []", b"port: 8888\nsocks-port: 8889\nmixed-port: 8890"):
            config = yaml.safe_load(transform(raw, Settings.load({})))
            self.assertNotIn("port", config)
            self.assertNotIn("socks-port", config)
            self.assertEqual(config["mixed-port"], 7890)
            config = yaml.safe_load(transform(raw, Settings.load({"CLASH_MIXED_PORT": "0", "CLASH_HTTP_PORT": "8080", "CLASH_SOCKS_PORT": "1080"})))
            self.assertNotIn("mixed-port", config)
            self.assertEqual(config["port"], 8080)
            self.assertEqual(config["socks-port"], 1080)

    def test_schedule(self):
        for env, expected in (({}, "0 3 * * *"), ({"CLASH_UPDATE_INTERVAL": "1"}, "0 * * * *"),
                              ({"CLASH_UPDATE_INTERVAL": "8"}, "0 */8 * * *"),
                              ({"CLASH_UPDATE_INTERVAL": "24"}, "0 3 * * *"),
                              ({"CLASH_CRON_TEST": "1"}, "* * * * *")):
            self.assertEqual(cron_schedule(Settings.load(env)), expected)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runtime = Runtime(Settings.load({}), root / "runtime", root / "mount", root / "example.yaml")
        self.runtime.directory.mkdir()
        self.old = b"rules:\n  - MATCH,DIRECT\nsecret: old\n"
        self.runtime.target.write_bytes(self.old)
        self.runtime.backup.parent.mkdir()
        self.runtime.backup.write_bytes(self.old)
        # Exercise flock on Linux; transactions remain testable on Windows.
        if os.name == "nt":
            self.runtime.lock = contextlib.nullcontext
        self.validation = patch("clash_runtime.runtime.validate")
        self.validation.start()
        self.addCleanup(self.validation.stop)

    def test_download_and_validation_failure_preserve_files(self):
        for failure in (ConfigError("download"),):
            with patch("clash_runtime.runtime.download", side_effect=failure), self.assertRaises(ConfigError):
                self.runtime.update()
        for raw in (b"[]", b"a: ["):
            with patch("clash_runtime.runtime.download", return_value=raw), self.assertRaises(ConfigError):
                self.runtime.update()
        with patch("clash_runtime.runtime.download", return_value=b"rules: []"), patch("clash_runtime.runtime.validate", side_effect=ConfigError("invalid")), self.assertRaises(ConfigError):
            self.runtime.update()
        self.assertEqual(self.runtime.target.read_bytes(), self.old)
        self.assertEqual(self.runtime.backup.read_bytes(), self.old)

    def test_reload_rollback(self):
        with patch("clash_runtime.runtime.download", return_value=b"rules: []"), patch.object(self.runtime, "reload", side_effect=[OSError(), None]) as reload:
            with self.assertRaises(ConfigError):
                self.runtime.update()
            self.assertEqual(reload.call_count, 2)
        self.assertEqual(self.runtime.target.read_bytes(), self.old)
        self.assertEqual(self.runtime.backup.read_bytes(), self.old)

    def test_update_success(self):
        with patch("clash_runtime.runtime.download", return_value=b"rules: []"), patch.object(self.runtime, "reload"):
            self.runtime.update()
        self.assertEqual(self.runtime.target.read_bytes(), self.runtime.backup.read_bytes())
        self.assertEqual(yaml.safe_load(self.runtime.target.read_bytes())["mixed-port"], 7890)

    def test_update_preserves_configured_multi_user_authentication(self):
        self.runtime.settings = Settings.load({"CLASH_PROXY_USERS_JSON": '[{"username":"alice","password":"a"},{"username":"bob","password":"b"}]'})
        with patch("clash_runtime.runtime.download", return_value=b"authentication: [old]\nskip-auth-prefixes: [127.0.0.0/8]"), patch.object(self.runtime, "reload"):
            self.runtime.update()
        config = yaml.safe_load(self.runtime.target.read_bytes())
        self.assertEqual(config["authentication"], ["alice:a", "bob:b"])
        self.assertEqual(config["skip-auth-prefixes"], [])
        self.assertEqual(self.runtime.target.read_bytes(), self.runtime.backup.read_bytes())

    def test_local_and_subscription_fallback(self):
        self.runtime.settings = Settings.load({"CLASH_SUBSCRIPTION_URL": "https://private.invalid/token"})
        with patch("clash_runtime.runtime.download", side_effect=ConfigError("failed")):
            self.runtime.initialize()
        self.assertEqual(yaml.safe_load(self.runtime.target.read_bytes())["secret"], "old")

    def test_example_fallback_and_geo_mount(self):
        self.runtime.backup.write_bytes(b"[]")
        self.runtime.example.write_bytes(b"rules: []")
        self.runtime.initialize()
        self.assertEqual(yaml.safe_load(self.runtime.target.read_bytes())["rules"], [])
        for name in ("geoip.metadb", "geosite.dat"):
            (self.runtime.mounted / name).write_bytes(b"mounted-data")
        self.runtime.geo_files()
        self.assertEqual((self.runtime.directory / "geoip.metadb").read_bytes(), b"mounted-data")

    def test_backup_failure_reports_warning(self):
        with patch("clash_runtime.runtime.atomic_write", side_effect=OSError()), patch("clash_runtime.runtime.log") as logger:
            self.runtime.sync(b"data")
            self.assertIn("WARNING", logger.call_args.args[0])

    @unittest.skipIf(os.name == "nt", "flock is a Linux container facility")
    def test_concurrent_update(self):
        with self.runtime.lock(), self.assertRaises(ConfigError):
            with self.runtime.lock():
                pass

    def test_validation_failure(self):
        with patch("clash_runtime.configuration.subprocess.run", return_value=Mock(returncode=1)), self.assertRaises(ConfigError):
            validate(b"rules: []", self.runtime.directory)
        self.assertEqual(list(self.runtime.directory.glob(".candidate-*")), [])


class TransportTests(unittest.TestCase):
    def test_resident_entrypoint_does_not_import_worker_dependencies(self):
        entrypoint = Path(__file__).resolve().parents[1] / "scripts" / "clash.py"
        code = (
            "import runpy, sys; "
            f"runpy.run_path({str(entrypoint)!r}, run_name='import_check'); "
            "assert not ({'yaml', 'ssl', 'urllib.request', "
            "'clash_runtime.runtime', 'clash_runtime.configuration'} & sys.modules.keys())"
        )
        subprocess.run([sys.executable, "-c", code], check=True,
                       env={**os.environ, "PYTHONPATH": str(entrypoint.parent)})

    def test_failed_initializer_prevents_mihomo_start(self):
        initializer = Mock(pid=100, returncode=1)
        initializer.poll.return_value = 1
        with patch("clash_runtime.supervisor.subprocess.Popen", return_value=initializer) as popen, patch("clash_runtime.supervisor.signal.signal"), patch("clash_runtime.supervisor.os.killpg", create=True), self.assertRaisesRegex(ConfigError, "initialization failed"):
            run(Settings.load({}))
        self.assertEqual(popen.call_count, 1)
        initializer.wait.assert_called_once()

    def test_safe_download_failure_reasons(self):
        private = "https://private.invalid/secret-token"
        cases = [
            (urllib.error.HTTPError(private, 403, private, {}, None), "HTTP 403"),
            (urllib.error.URLError(socket.gaierror(private)), "DNS lookup failed"),
            (urllib.error.URLError(ssl.SSLCertVerificationError(private)), "TLS certificate verification failed"),
            (TimeoutError(private), "connection timed out"),
            (ValueError(private), "invalid URL or request format"),
        ]
        for error, expected in cases:
            self.assertIn(expected, failure_reason(error))
            self.assertNotIn(private, failure_reason(error))

    def test_browser_user_agent_and_override(self):
        for env, expected in (({}, DEFAULT_USER_AGENT), ({"SUBSCRIPTION_USER_AGENT": "custom-client"}, "custom-client")):
            with patch("clash_runtime.subscription.urllib.request.build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = b"ok"
                fetch("https://example.invalid", Settings.load(env))
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header("User-agent"), expected)

    def test_first_url(self):
        with patch("clash_runtime.subscription.fetch", return_value=b"ok") as fetch_mock:
            download(Settings.load({"CLASH_SUBSCRIPTION_URL": "https://a,https://b;https://c"}))
        self.assertEqual(fetch_mock.call_args.args[0], "https://a")

    def test_conversion_fallback(self):
        settings = Settings.load({"CLASH_SUBSCRIPTION_URL": "https://a\nhttps://b", "SUBCONVERTER_ENABLED": "true"})
        with patch("clash_runtime.subscription.fetch", side_effect=[ConfigError("failed"), b"ok"]) as fetch_mock:
            self.assertEqual(download(settings), b"ok")
        self.assertIn("%7C", fetch_mock.call_args_list[0].args[0])

    def test_retries(self):
        with patch("clash_runtime.subscription.urllib.request.build_opener") as opener, patch("clash_runtime.subscription.time.sleep"):
            opener.return_value.open.side_effect = OSError("private URL")
            with self.assertRaisesRegex(ConfigError, "after retries"):
                fetch("https://private", Settings.load({}))
            self.assertEqual(opener.return_value.open.call_count, 3)

    def test_custom_controller_and_secret(self):
        settings = Settings.load({"CLASH_EXTERNAL_CONTROLLER_PORT": "9999", "CLASH_API_SECRET": "token"})
        with patch("clash_runtime.subscription.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.status = 200
            self.assertEqual(api_request(settings, "/version"), 200)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:9999/version")
            self.assertEqual(request.get_header("Authorization"), "Bearer token")
        with patch("clash_runtime.runtime.api_request", return_value=200) as api:
            healthcheck(settings)
            self.assertEqual(api.call_args.args[0].api, "http://127.0.0.1:9999")

    def test_supervisor_reaps_children(self):
        child = Mock()
        child.poll.return_value = 7
        initializer = Mock(returncode=0)
        initializer.poll.return_value = 0
        with patch("clash_runtime.supervisor.subprocess.Popen", side_effect=[initializer, child]) as popen, patch("clash_runtime.supervisor.signal.signal"), patch("clash_runtime.supervisor.os.killpg", create=True):
            self.assertEqual(run(Settings.load({})), 7)
            child.wait.assert_called_once()
            self.assertEqual(popen.call_args_list[0].args[0][-1], "initialize")

    def test_supervisor_forwards_stop_and_waits(self):
        child = Mock(pid=123)
        child.poll.return_value = None
        handlers = {}
        def register(sig, handler):
            handlers[sig] = handler
        def stop_on_sleep(seconds):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        initializer = Mock(returncode=0)
        initializer.poll.return_value = 0
        with patch("clash_runtime.supervisor.subprocess.Popen", side_effect=[initializer, child]), patch("clash_runtime.supervisor.signal.signal", side_effect=register), patch("clash_runtime.supervisor.os.killpg", create=True) as kill, patch("clash_runtime.supervisor.time.sleep", side_effect=stop_on_sleep):
            self.assertEqual(run(Settings.load({})), 0)
            kill.assert_any_call(123, signal.SIGTERM)
            child.wait.assert_called_once()

    def test_cron_is_not_a_session_leader(self):
        mihomo, cron = Mock(pid=101), Mock(pid=102)
        mihomo.poll.return_value = 0
        cron.poll.return_value = None
        initializer = Mock(returncode=0)
        initializer.poll.return_value = 0
        with patch("clash_runtime.supervisor.atomic_write"), patch("clash_runtime.supervisor.subprocess.Popen", side_effect=[initializer, mihomo, cron]) as popen, patch("clash_runtime.supervisor.signal.signal"), patch("clash_runtime.supervisor.os.killpg", create=True):
            run(Settings.load({"CLASH_SUBSCRIPTION_URL": "https://example.invalid/sub"}))
        self.assertTrue(popen.call_args_list[0].kwargs["start_new_session"])
        self.assertEqual(popen.call_args_list[2].args[0], ["crond", "-f"])
        self.assertFalse(popen.call_args_list[2].kwargs["start_new_session"])
        self.assertEqual(popen.call_args_list[2].kwargs["process_group"], 0)


if __name__ == "__main__":
    unittest.main()
