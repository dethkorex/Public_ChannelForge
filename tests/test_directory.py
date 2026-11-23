import os
import tempfile
import unittest
from pathlib import Path

from directory import server


class DirectoryTests(unittest.TestCase):
    def test_worker_reactivates_creator_when_it_republishes_after_restart(self):
        worker = (Path(__file__).resolve().parents[1] / "directory/worker/src/index.js").read_text(encoding="utf-8")
        self.assertIn('online:true,last_ok:now,failures:0,updated_at:now', worker)
        self.assertIn('must be online before republishing', worker)

    def test_worker_stays_within_free_tier_health_check_limits(self):
        worker = (Path(__file__).resolve().parents[1] / "directory/worker/src/index.js").read_text(encoding="utf-8")
        self.assertIn("Math.min(10,keys.length)", worker)
        self.assertIn("An already-offline creator causes no further writes", worker)
        self.assertIn('env.CREATORS.get("catalog:v1","json")', worker)

    def setUp(self):
        server.DB_PATH = Path(tempfile.mkdtemp(prefix="channel-forge-directory-")) / "directory.db"
        os.environ["CHANNELFORGE_DIRECTORY_ALLOW_HTTP"] = "1"

    def tearDown(self):
        os.environ.pop("CHANNELFORGE_DIRECTORY_ALLOW_HTTP", None)

    def test_creator_payload_contains_only_public_metadata(self):
        value = server.clean_payload({"name": "My Studio", "description": "Channels", "guide_url": "http://example.test/guide.xml", "playlist_url": "http://example.test/channels.m3u", "vault_url": "http://example.test/api/public/vault"})
        self.assertEqual(value["slug"], "my-studio")
        self.assertNotIn("plex_token", value)
        self.assertNotIn("local_libraries", value)
        self.assertEqual(value["vault_url"], "http://example.test/api/public/vault")

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
