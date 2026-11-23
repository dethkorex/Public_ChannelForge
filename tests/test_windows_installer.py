import unittest
import tempfile
from pathlib import Path
from unittest import mock

from installer import windows_installer


class WindowsInstallerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
