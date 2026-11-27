import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import tester_updates as updates


class TesterUpdateTests(unittest.TestCase):
    def test_only_manual_request_fetches_manifest(self):
        config = {"revision": 17, "version": "4.1.7", "report_url": "https://example.com/" + "a" * 32 + "/report"}
        release = {"revision": 18, "version": "4.0.0", "bytes": 4, "sha256": "b" * 64}
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({updates.ARTIFACT: release}).encode())
        with tempfile.TemporaryDirectory() as folder, patch.object(updates, "install_root", return_value=Path(folder)), \
                patch.object(updates.urllib.request, "build_opener", return_value=opener):
            self.assertIsNone(updates.installed_runtime(config))
            opener.open.assert_not_called()
            self.assertEqual(updates.private_release(config), release)
            self.assertTrue(opener.open.call_args.args[0].endswith("/manifest.json?manual=1"))

    def test_installed_patch_notes_are_local_and_manual_check_returns_new_release_notes(self):
        config = {"revision": 12, "version": "4.1.0", "release_notes": ["Installed notes."]}
        release = {"revision": 13, "version": "4.1.2", "release_notes": ["Available notes."]}
        headers = {"Host": "127.0.0.1:8005", "X-ChannelForge-Tester-Update": "1"}
        with patch.object(updates, "settings", return_value=config), \
                patch.object(updates, "private_release", return_value=release) as read:
            self.assertEqual(updates.local_update("GET", "/api/tester-update/installed", headers, 8005, component="tv"),
                             (200, {"component": "tv", "revision": 12, "version": "4.1.0", "display_version": "Alpha V4.1",
                                    "release_notes": ["Installed notes."]}))
            read.assert_not_called()
            self.assertEqual(updates.local_update("GET", "/api/tester-update", headers, 8005),
                             (200, {"current": 12, "available": 13, "version": "4.1.2", "display_version": "Alpha V4.1.2",
                                    "release_notes": ["Available notes."]}))
            read.assert_called_once_with(config)

    def test_checksum_failure_does_not_activate_or_leave_partial_update(self):
        config = {"revision": 6, "report_url": "https://example.com/" + "a" * 32 + "/report"}
        payload = b"tester fixture"
        release = {"revision": 7, "bytes": len(payload), "sha256": hashlib.sha256(b"wrong").hexdigest()}
        opener = Mock()
        opener.open.return_value = io.BytesIO(payload)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(updates.urllib.request, "build_opener", return_value=opener):
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "checksum"):
                updates.stage_release(root, config, release, Mock())
            self.assertEqual(list(root.iterdir()), [])

    def test_restart_waits_for_previous_app_and_launches_both(self):
        with tempfile.TemporaryDirectory() as folder:
            install = Path(folder)
            root = install / "updates"
            revision = 7
            runtime = updates.runtime_for(root, revision)
            runtime.parent.mkdir(parents=True)
            runtime.touch()
            with patch.object(updates, "install_root", return_value=install), \
                    patch.object(updates, "settings", return_value={"revision": revision}), \
                    patch.object(updates, "launch_component") as launch:
                with updates.exclusive_file(root / "running-6-12345.lock"):
                    restart = threading.Thread(target=updates.restart_suite, args=(revision,))
                    restart.start()
                    deadline = time.monotonic() + 5
                    while not (root / "current").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertEqual(updates.installed_runtime({"revision": 6}), runtime)
                    launch.assert_not_called()
                restart.join(timeout=5)
                self.assertFalse(restart.is_alive())
                self.assertEqual([call.args for call in launch.call_args_list], [("studio", runtime), ("tv", runtime)])

    def test_update_route_rejects_non_local_browser_origins(self):
        headers = {"Host": "127.0.0.1:8005", "Origin": "https://elsewhere.example",
                   "X-ChannelForge-Tester-Update": "1"}
        with patch.object(updates, "private_release") as read:
            self.assertEqual(updates.local_update("GET", "/api/tester-update", headers, 8005)[0], 403)
            read.assert_not_called()
