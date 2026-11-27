from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tester_diagnostics as diagnostics


class TesterDiagnosticsTests(unittest.TestCase):
    def test_opt_in_is_reversible_and_clears_pending_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with patch.object(diagnostics, "preferences_dir", return_value=folder):
                self.assertFalse(diagnostics.automatic_reports())
                self.assertTrue(diagnostics.automatic_reports(True))
                pending = folder / "crash-studio.json"
                pending.write_text("test")
                self.assertFalse(diagnostics.automatic_reports(False))
                self.assertFalse(pending.exists())

    def test_crash_report_keeps_class_and_code_but_excludes_sensitive_fields(self):
        report = {"report_type": "crash", "platform": "linux", "app": "4.0.0", "crash_area": "studio",
                  "crash_kind": "RuntimeError", "crash_line": 221, "url": "https://private.example", "message": "private",
                  "timeline": [{"kind": "error", "ms": 1234}, {"kind": "unsupported", "ms": 5}]}
        clean = diagnostics.clean_report(report)
        self.assertEqual(clean["crash_kind"], "RuntimeError")
        self.assertEqual(clean["crash_line"], 221)
        self.assertEqual(clean["timeline"], [{"kind": "error", "ms": 1234}])
        self.assertNotIn("url", clean)
        self.assertNotIn("message", clean)
        with self.assertRaises(ValueError):
            diagnostics.clean_report({"platform": []})

    def test_delivery_route_accepts_only_route_labels(self):
        for route in ("preferred-relay", "registered-route", "registered-fallback"):
            with self.subTest(route=route):
                clean = diagnostics.clean_report({"platform": "fire-tv", "delivery_route": route})
                self.assertEqual(clean["delivery_route"], route)
        clean = diagnostics.clean_report({"platform": "fire-tv", "delivery_route": "https://private.example/stream"})
        self.assertNotIn("delivery_route", clean)
