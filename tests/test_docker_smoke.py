"""Validate smoke-check expectations without requiring Docker."""
import unittest
from unittest.mock import patch

from docker_smoke import assert_http_status


class SmokeAssertionTests(unittest.TestCase):
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
