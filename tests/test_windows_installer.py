import unittest

from installer import windows_installer


class WindowsInstallerTests(unittest.TestCase):
    def test_creator_setup_selects_creator_branding(self):
        self.assertEqual(windows_installer.product_key("ChannelForge_Creator_Setup.exe"), "creator")

    def test_tv_setup_selects_tv_branding(self):
        self.assertEqual(windows_installer.product_key("ChannelForge_TV_Setup.exe"), "tv")

    def test_each_product_has_its_own_icon_and_install_folder(self):
        creator = windows_installer.PRODUCTS["creator"]
        tv = windows_installer.PRODUCTS["tv"]
        self.assertNotEqual(creator["icon"], tv["icon"])
        self.assertNotEqual(creator["folder"], tv["folder"])
        self.assertEqual(tv["app"], "ChannelForge_TV")


if __name__ == "__main__":
    unittest.main()
