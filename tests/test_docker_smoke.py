"""Validate smoke-check expectations without requiring Docker."""
import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from docker_smoke import assert_http_status, INITIALIZER_PROBE


class SmokeAssertionTests(unittest.TestCase):
    def test_proc_probe_matches_argv_and_ignores_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, args in {
                '1': b'python3\0/app/scripts/clash.py\0run\0',
                '2': b'python3\0/app/scripts/clash.py\0initialize\0',
                '3': b'python3\0-c\0' + INITIALIZER_PROBE.encode() + b'\0',
                '4': b'',
            }.items():
                (root / pid).mkdir()
                (root / pid / 'cmdline').write_bytes(args)
            def probe():
                result = subprocess.run([sys.executable, '-c', INITIALIZER_PROBE, directory],
                                        check=True, capture_output=True, text=True)
                return json.loads(result.stdout)
            self.assertEqual(probe(), ['2'])
            (root / '2' / 'cmdline').unlink()
            self.assertEqual(probe(), [])

    def test_missing_and_invalid_credentials_have_distinct_statuses(self):
        with patch("docker_smoke.request", return_value=(0, "407")):
            assert_http_status("127.0.0.1:7890", "407")
        with patch("docker_smoke.request", return_value=(0, "403")):
            assert_http_status("127.0.0.1:7890", "403", "user:wrong")

    def test_wrong_status_and_transport_failures_are_not_accepted(self):
        for actual in ((0, "200"), (0, "407"), (7, "000")):
            with self.subTest(actual=actual), patch("docker_smoke.request", return_value=actual):
                with self.assertRaisesRegex(AssertionError, "expected curl=0 HTTP=403") as error:
                    assert_http_status("127.0.0.1:7890", "403", "user:private-password")
                self.assertNotIn("private-password", str(error.exception))
