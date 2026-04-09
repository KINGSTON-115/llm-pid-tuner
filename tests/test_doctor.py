import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import doctor
from core.i18n import set_language


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300


class DoctorTests(unittest.TestCase):
    def setUp(self):
        set_language("en")

    def test_summarize_doctor_checks_counts_statuses(self):
        checks = [
            doctor.DoctorCheck("a", "PASS", "ok"),
            doctor.DoctorCheck("b", "WARN", "warn"),
            doctor.DoctorCheck("c", "FAIL", "fail"),
        ]
        summary = doctor.summarize_doctor_checks(checks)
        self.assertIn("1 pass", summary)
        self.assertIn("1 warn", summary)
        self.assertIn("1 fail", summary)

    def test_collect_doctor_checks_reports_reachable_api(self):
        fake_port = types.SimpleNamespace(device="COM7")
        with patch.object(doctor, "initialize_runtime_config"):
            with patch.dict(
                doctor.CONFIG,
                {
                    "LLM_API_KEY": "sk-test",
                    "LLM_API_BASE_URL": "https://example.com/v1",
                    "LLM_MODEL_NAME": "demo-model",
                    "LLM_PROVIDER": "openai",
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "ALL_PROXY": "",
                    "NO_PROXY": "",
                },
                clear=False,
            ):
                with patch("doctor.os.path.exists", return_value=True):
                    with patch("doctor.requests.get", return_value=FakeResponse(200)):
                        with patch(
                            "doctor.serial.tools.list_ports.comports",
                            return_value=[fake_port],
                        ):
                            checks = doctor.collect_doctor_checks()

        status_by_name = {check.name: check.status for check in checks}
        self.assertEqual(status_by_name["config.json"], "PASS")
        self.assertEqual(status_by_name["config fields"], "PASS")
        self.assertEqual(status_by_name["API reachability"], "PASS")
        self.assertEqual(status_by_name["serial ports"], "PASS")


if __name__ == "__main__":
    unittest.main()
