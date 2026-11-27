import unittest

import diagnostic_metrics as diagnostics


class DiagnosticMetricsTests(unittest.TestCase):
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

    def test_only_known_numeric_response_headers_reach_viewer_reports(self):
        headers = {"X-ChannelForge-Relay": "vps", "X-ChannelForge-Cache": "MISS",
                   "X-ChannelForge-Upstream-Header-Time": "0.842", "X-ChannelForge-Studio-Response-Ms": "214",
                   "Authorization": "private"}
        self.assertEqual(diagnostics.playback_response_headers(headers),
                         {key: value for key, value in headers.items() if key.startswith("X-ChannelForge-")})
        self.assertEqual(diagnostics.playback_response_headers({
            "X-ChannelForge-Relay": "https://private.example", "X-ChannelForge-Cache": "unknown",
            "X-ChannelForge-Upstream-Header-Time": "nan", "X-ChannelForge-Studio-Response-Ms": "0\r\nSet-Cookie:"}), {})
