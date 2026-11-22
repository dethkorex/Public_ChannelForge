import os
import tempfile
import unittest
from pathlib import Path

from directory import server


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        server.DB_PATH = Path(tempfile.mkdtemp(prefix="channel-forge-directory-")) / "directory.db"
        os.environ["CHANNELFORGE_DIRECTORY_ALLOW_HTTP"] = "1"

    def tearDown(self):
        os.environ.pop("CHANNELFORGE_DIRECTORY_ALLOW_HTTP", None)

    def test_creator_payload_contains_only_public_metadata(self):
        value = server.clean_payload({"name": "My Studio", "description": "Channels", "guide_url": "http://example.test/guide.xml", "playlist_url": "http://example.test/channels.m3u"})
        self.assertEqual(value["slug"], "my-studio")
        self.assertNotIn("plex_token", value)
        self.assertNotIn("local_libraries", value)

    def test_production_rejects_non_https_endpoints(self):
        os.environ.pop("CHANNELFORGE_DIRECTORY_ALLOW_HTTP", None)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            server.clean_url("http://example.test/guide.xml")

    def test_database_stores_hashed_creator_key(self):
        with server.connection() as database:
            columns = {row[1] for row in database.execute("PRAGMA table_info(creators)")}
        self.assertIn("key_hash", columns)
        self.assertNotIn("api_key", columns)


if __name__ == "__main__":
    unittest.main()
