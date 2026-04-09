import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.doctoring import (
    DoctorCheck,
    _collect_matlab_checks,
    collect_doctor_checks,
    mask_secret,
    models_endpoint,
    print_doctor_report,
    summarize_doctor_checks,
)


def _identity_tr(zh: str, en: str) -> str:
    return en


class MaskSecretTests(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(mask_secret(""), "<empty>")

    def test_short_string_all_stars(self):
        self.assertEqual(mask_secret("abc"), "***")
        self.assertEqual(mask_secret("abcdefgh"), "********")

    def test_long_string_partial_mask(self):
        self.assertEqual(mask_secret("sk-abcdefghijkl"), "sk-a...ijkl")


class ModelsEndpointTests(unittest.TestCase):
    def test_openai_endpoint_uses_authorization(self):
        url, headers = models_endpoint("openai", "https://api.example.com", "sk-123")
        self.assertEqual(url, "https://api.example.com/models")
        self.assertEqual(headers["Authorization"], "Bearer sk-123")

    def test_anthropic_endpoint_adds_v1(self):
        url, headers = models_endpoint("anthropic", "https://api.anthropic.com", "key")
        self.assertEqual(url, "https://api.anthropic.com/v1/models")
        self.assertEqual(headers["x-api-key"], "key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")

    def test_anthropic_preserves_existing_v1(self):
        url, _ = models_endpoint("anthropic", "https://api.anthropic.com/v1", "k")
        self.assertEqual(url, "https://api.anthropic.com/v1/models")

    def test_trailing_slash_stripped(self):
        url, _ = models_endpoint("openai", "https://api.example.com/", "sk")
        self.assertEqual(url, "https://api.example.com/models")

    def test_default_provider_is_openai(self):
        _, headers = models_endpoint("", "https://x.com", "k")
        self.assertIn("Authorization", headers)


class CollectMatlabChecksTests(unittest.TestCase):
    def test_empty_model_path_returns_empty(self):
        checks = _collect_matlab_checks({}, tr_fn=_identity_tr, path_exists=lambda p: False)
        self.assertEqual(checks, [])

    def test_valid_model_reports_pass(self):
        config = {
            "MATLAB_MODEL_PATH": "/path/to/model.slx",
            "MATLAB_OUTPUT_SIGNAL": "out",
            "MATLAB_PID_BLOCK_PATH": "m/PID",
        }
        checks = _collect_matlab_checks(
            config, tr_fn=_identity_tr, path_exists=lambda p: True
        )
        names = {c.name: c.status for c in checks}
        self.assertEqual(names["MATLAB model"], "PASS")
        self.assertEqual(names["MATLAB output signal"], "PASS")
        self.assertEqual(names["PID block path"], "PASS")

    def test_missing_output_signal_fails(self):
        config = {"MATLAB_MODEL_PATH": "m.slx"}
        checks = _collect_matlab_checks(
            config, tr_fn=_identity_tr, path_exists=lambda p: True
        )
        signal_check = next(c for c in checks if c.name == "MATLAB output signal")
        self.assertEqual(signal_check.status, "FAIL")

    def test_missing_all_pid_paths_warns_and_mentions_auto_discovery(self):
        config = {"MATLAB_MODEL_PATH": "m.slx", "MATLAB_OUTPUT_SIGNAL": "out"}
        checks = _collect_matlab_checks(
            config, tr_fn=_identity_tr, path_exists=lambda p: True
        )
        pid_check = next(c for c in checks if c.name == "PID block path")
        self.assertEqual(pid_check.status, "WARN")
        self.assertIn("auto-discovery", pid_check.detail)


class SummarizeDoctorChecksTests(unittest.TestCase):
    def test_counts_pass_warn_fail(self):
        checks = [
            DoctorCheck("a", "PASS", ""),
            DoctorCheck("b", "PASS", ""),
            DoctorCheck("c", "WARN", ""),
            DoctorCheck("d", "FAIL", ""),
        ]
        summary = summarize_doctor_checks(checks, tr_fn=_identity_tr)
        self.assertIn("2 pass", summary)
        self.assertIn("1 warn", summary)
        self.assertIn("1 fail", summary)


class PrintDoctorReportTests(unittest.TestCase):
    def test_pass_only_returns_zero(self):
        lines: list[str] = []
        rc = print_doctor_report(
            [DoctorCheck("a", "PASS", "ok")],
            tr_fn=_identity_tr,
            printer=lines.append,
        )
        self.assertEqual(rc, 0)
        self.assertTrue(any("successfully" in line for line in lines))

    def test_warn_returns_zero_with_warn_message(self):
        lines: list[str] = []
        rc = print_doctor_report(
            [DoctorCheck("a", "WARN", "hm")],
            tr_fn=_identity_tr,
            printer=lines.append,
        )
        self.assertEqual(rc, 0)
        self.assertTrue(any("WARN" in line for line in lines))

    def test_fail_returns_one(self):
        lines: list[str] = []
        rc = print_doctor_report(
            [DoctorCheck("a", "FAIL", "bad")],
            tr_fn=_identity_tr,
            printer=lines.append,
        )
        self.assertEqual(rc, 1)
        self.assertTrue(any("FAIL" in line for line in lines))


class CollectDoctorChecksIntegrationTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return {
            "LLM_API_KEY": "sk-abcdefghijkl",
            "LLM_API_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL_NAME": "demo",
            "LLM_PROVIDER": "openai",
        }

    def _run(self, config, **overrides):
        kwargs = dict(
            config_path="/tmp/config.json",
            tr_fn=_identity_tr,
            initialize_runtime_config_fn=MagicMock(),
            requests_get=MagicMock(return_value=types.SimpleNamespace(status_code=200, ok=True)),
            list_serial_ports=lambda: [],
            path_exists=lambda p: True,
            getenv=lambda key: None,
        )
        kwargs.update(overrides)
        return collect_doctor_checks(config, **kwargs)

    def test_healthy_config_passes(self):
        checks = self._run(self._base_config())
        names = {c.name: c.status for c in checks}
        self.assertEqual(names["config.json"], "PASS")
        self.assertEqual(names["config fields"], "PASS")
        self.assertEqual(names["API reachability"], "PASS")

    def test_placeholder_api_key_fails(self):
        config = self._base_config()
        config["LLM_API_KEY"] = "your-api-key-here"
        checks = self._run(config)
        field_check = next(c for c in checks if c.name == "config fields")
        self.assertEqual(field_check.status, "FAIL")

    def test_empty_base_url_fails_reachability(self):
        config = self._base_config()
        config["LLM_API_BASE_URL"] = ""
        checks = self._run(config)
        api_check = next(c for c in checks if c.name == "API reachability")
        self.assertEqual(api_check.status, "FAIL")

    def test_request_exception_reports_fail(self):
        def raise_exc(*args, **kwargs):
            raise RuntimeError("boom")

        checks = self._run(self._base_config(), requests_get=raise_exc)
        api_check = next(c for c in checks if c.name == "API reachability")
        self.assertEqual(api_check.status, "FAIL")


if __name__ == "__main__":
    unittest.main()
