import unittest
import tempfile
import hashlib
import os
from pathlib import Path
from unittest import mock

from installer import windows_installer
import windows_distribution


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {
            "USERPROFILE": str(self.root),
            "LOCALAPPDATA": str(self.root / "Local"),
            "APPDATA": str(self.root / "Roaming"),
        })
        registry = mock.patch.object(windows_installer, "winreg", None)
        for patch in (environment, registry):
            patch.start()
            self.addCleanup(patch.stop)

    def test_creator_setup_selects_creator_branding(self):
        self.assertEqual(windows_installer.product_key("ChannelForge_Creator_Setup.exe"), "creator")

    def test_tv_setup_selects_tv_branding(self):
        self.assertEqual(windows_installer.product_key("ChannelForge_TV_Setup.exe"), "tv")

    def test_unified_setup_selects_suite_branding(self):
        self.assertEqual(windows_installer.product_key("ChannelForge_Setup.exe"), "suite")

    def test_windows_build_bundles_both_apps_in_unified_setup(self):
        script = (Path(__file__).parents[1] / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("--name ChannelForge_Setup", script)
        self.assertIn('installer\\payload\\ChannelForge_Creator.exe;payload', script)
        self.assertIn('installer\\payload\\ChannelForge_TV.exe;payload', script)
        self.assertIn('ChannelForge_Creator_Setup.exe', script)
        self.assertIn('ChannelForge_TV_Setup.exe', script)

    def test_each_product_has_its_own_icon_and_install_folder(self):
        creator = windows_installer.PRODUCTS["creator"]
        tv = windows_installer.PRODUCTS["tv"]
        self.assertNotEqual(creator["icon"], tv["icon"])
        self.assertEqual(creator["folder"], tv["folder"])
        self.assertEqual(tv["app"], "ChannelForge_TV")
        self.assertEqual(windows_installer.INSTALL_PRODUCT_KEYS, ("creator", "tv"))

    def test_installer_removes_legacy_shortcuts_and_uses_registry_autostart(self):
        source = (Path(__file__).parents[1] / "installer" / "windows_installer.py").read_text(encoding="utf-8")
        self.assertIn("clean_legacy_shortcuts", source)
        self.assertIn("Software\\Microsoft\\Windows\\CurrentVersion\\Run", source)
        self.assertIn("startup_shortcut.unlink", source)

    def test_silent_update_reuses_existing_creator_executable(self):
        folder = Path(tempfile.mkdtemp(prefix="channel-forge-installed-"))
        target = folder / "ChannelForge_Creator.exe"
        target.write_bytes(b"installed")
        parsed = windows_installer.silent_update_target(["--silent-update", "--target-exe", str(target), "--wait-pid", "321"])
        self.assertEqual(parsed, (target.resolve(), 321, "creator"))

    def test_regular_setup_does_not_enter_silent_update(self):
        self.assertIsNone(windows_installer.silent_update_target([]))

    def test_silent_update_replaces_existing_executable_without_wizard(self):
        folder = Path(tempfile.mkdtemp(prefix="channel-forge-update-test-"))
        target = folder / "ChannelForge_Creator.exe"
        payload = folder / "new-creator.exe"
        target.write_bytes(b"old-version")
        payload.write_bytes(b"new-version")
        icon = folder / "source.ico"
        icon.write_bytes(b"icon")
        with mock.patch.object(windows_installer, "payload_for", return_value=payload), mock.patch.object(windows_installer, "icon_for", return_value=icon), mock.patch.object(windows_installer.subprocess, "Popen") as launch:
            windows_installer.apply_silent_update(target, 321)
        self.assertEqual(target.read_bytes(), b"new-version")
        launch.assert_called_once_with([str(target)], cwd=str(folder))

    def test_silent_update_replaces_both_installed_apps(self):
        folder = Path(tempfile.mkdtemp(prefix="channel-forge-suite-update-"))
        creator = folder / "ChannelForge_Creator.exe"
        tv = folder / "ChannelForge_TV.exe"
        creator.write_bytes(b"old-creator")
        tv.write_bytes(b"old-tv")
        payloads = {"creator": folder / "creator-payload.exe", "tv": folder / "tv-payload.exe"}
        payloads["creator"].write_bytes(b"new-creator")
        payloads["tv"].write_bytes(b"new-tv")
        icon = folder / "source.ico"
        icon.write_bytes(b"icon")
        with mock.patch.object(windows_installer, "payload_for", side_effect=lambda key: payloads[key]), mock.patch.object(windows_installer, "icon_for", return_value=icon), mock.patch.object(windows_installer.subprocess, "Popen"):
            windows_installer.apply_silent_update(tv, 44, "tv")
        self.assertEqual(creator.read_bytes(), b"new-creator")
        self.assertEqual(tv.read_bytes(), b"new-tv")

    def test_desktop_update_migrates_both_apps_and_reuses_shortcuts(self):
        desktop = self.root / "Desktop"
        desktop.mkdir()
        creator = desktop / "ChannelForge_Creator.exe"
        tv = desktop / "ChannelForge_TV.exe"
        unrelated = desktop / "ChannelForge.exe"
        for path in (creator, tv, unrelated, desktop / "ChannelForge_Creator.lnk"):
            path.write_bytes(b"old")
        payload = self.root / "new.exe"
        payload.write_bytes(b"new")
        icon = self.root / "new.ico"
        icon.write_bytes(b"icon")

        def shortcut(path, target, _icon):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(target))

        with mock.patch.object(windows_installer, "payload_for", return_value=payload), \
             mock.patch.object(windows_installer, "icon_for", return_value=icon), \
             mock.patch.object(windows_installer, "create_shortcut", side_effect=shortcut), \
             mock.patch.object(windows_installer, "stop_locked_channel_forge_apps"), \
             mock.patch.object(windows_installer.subprocess, "Popen") as launch:
            windows_installer.apply_silent_update(creator, 44)
            folder = windows_installer.default_installation()
            windows_installer.apply_silent_update(folder / creator.name, 45)

        self.assertEqual(windows_installer.registered_installation(), folder.resolve())
        self.assertEqual((folder / creator.name).read_bytes(), b"new")
        self.assertEqual((folder / tv.name).read_bytes(), b"new")
        self.assertEqual((desktop / "ChannelForge TV.lnk").read_text(), str(folder / tv.name))
        self.assertEqual(sorted(path.name for path in desktop.iterdir()), [
            "ChannelForge Creator.lnk", "ChannelForge TV.lnk", "ChannelForge.exe",
        ])
        launch.assert_called_once_with([str(folder / creator.name)], cwd=str(folder))

    def test_apps_share_verified_download_and_prune_only_old_updaters(self):
        directory = windows_distribution.windows_update_cache()
        setup = b"verified package"
        checksum = hashlib.sha256(setup).hexdigest()
        download = mock.Mock(side_effect=lambda path: path.write_bytes(setup))
        report = mock.Mock()
        first = windows_distribution.verified_windows_setup(directory, "3.5.7", checksum, download, report)
        second = windows_distribution.verified_windows_setup(directory, "3.5.7", checksum, download, report)
        self.assertEqual(first, second)
        self.assertEqual(list(directory.iterdir()), [first])
        self.assertEqual(download.call_count, 1)

        old_tv = self.root / "Local" / "ChannelForge_TV" / "updates" / "ChannelForge_TV-Setup-v3.5.2.exe"
        old_tv.parent.mkdir(parents=True)
        old_tv.write_bytes(setup)
        retained = directory / "ChannelForge_Setup-v3.5.8.exe"
        retained.write_bytes(setup)
        unrelated = directory / "notes.txt"
        unrelated.write_text("keep")
        windows_distribution.clean_windows_update_cache("3.5.7")
        self.assertFalse(first.exists())
        self.assertFalse(old_tv.exists())
        self.assertTrue(retained.exists())
        self.assertTrue(unrelated.exists())

    def test_silent_updater_closes_only_exact_installed_app_paths(self):
        source = (Path(__file__).parents[1] / "installer" / "windows_installer.py").read_text(encoding="utf-8")
        self.assertIn("stop_locked_channel_forge_apps", source)
        self.assertIn("$_.ExecutablePath.ToLower()", source)
        self.assertIn("force_close_at", source)

    def test_regular_installer_removes_obsolete_desktop_executables(self):
        source = (Path(__file__).parents[1] / "installer" / "windows_installer.py").read_text(encoding="utf-8")
        self.assertIn("clean_legacy_desktop_files", source)
        self.assertIn('"ChannelForge_Creator.exe", "ChannelForge_TV.exe"', source)


if __name__ == "__main__":
    unittest.main()
