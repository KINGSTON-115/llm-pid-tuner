import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import CONFIG
from sim.runtime import EVENT_LIFECYCLE, EVENT_SAMPLE, publish_event, reset_csv_exporter_for_tests


class CsvExportTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_csv_exporter_for_tests()

    def test_export_disabled_does_not_create_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "samples.csv"
            with patch.dict(CONFIG, {"CSV_EXPORT_PATH": ""}, clear=False):
                publish_event(
                    None,
                    EVENT_SAMPLE,
                    timestamp=1.0,
                    setpoint=200.0,
                    input=150.0,
                    pwm=100.0,
                    error=50.0,
                    p=1.0,
                    i=0.1,
                    d=0.05,
                )
            self.assertFalse(csv_path.exists())

    def test_sample_events_append_to_csv_with_session_and_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "samples.csv"
            config_patch = {"CSV_EXPORT_PATH": str(csv_path)}
            with patch.dict(CONFIG, config_patch, clear=False):
                publish_event(
                    None,
                    EVENT_LIFECYCLE,
                    phase="starting",
                    detail="Opening COM9 at 115200 baud.",
                )
                publish_event(
                    None,
                    EVENT_LIFECYCLE,
                    phase="collecting",
                    detail="Collecting data for round 1.",
                )
                publish_event(
                    None,
                    EVENT_SAMPLE,
                    timestamp=12.0,
                    setpoint=200.0,
                    input=150.0,
                    pwm=120.0,
                    error=50.0,
                    p=1.0,
                    i=0.1,
                    d=0.05,
                )
                publish_event(
                    None,
                    EVENT_SAMPLE,
                    timestamp=13.0,
                    setpoint=200.0,
                    input=151.0,
                    pwm=121.0,
                    error=49.0,
                    p=1.0,
                    i=0.1,
                    d=0.05,
                    p2=2.0,
                    i2=0.2,
                    d2=0.02,
                )
                publish_event(
                    None,
                    EVENT_LIFECYCLE,
                    phase="completed",
                    detail="Finished export.",
                )

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["mode"], "hardware")
            self.assertEqual(rows[0]["round"], "1")
            self.assertEqual(rows[0]["timestamp_ms"], "12.0")
            self.assertEqual(rows[1]["p2"], "2.0")
            self.assertEqual(rows[1]["i2"], "0.2")
            self.assertEqual(rows[1]["d2"], "0.02")
            self.assertTrue(rows[0]["session_id"])
