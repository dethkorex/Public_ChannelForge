import json
import os
import threading
import urllib.error
import urllib.request
from unittest import mock
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
        value = server.clean_payload({"name": "My Studio", "description": "Channels", "guide_url": "http://example.test/guide.xml", "playlist_url": "http://example.test/channels.m3u", "vault_url": "http://example.test/api/public/vault"})
        self.assertEqual(value["slug"], "my-studio")
        self.assertNotIn("plex_token", value)
        self.assertNotIn("local_libraries", value)
        self.assertEqual(value["vault_url"], "http://example.test/api/public/vault")

    def test_heartbeat_persistence_and_authenticated_unpublish(self):
        def serve():
            service = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=service.serve_forever, daemon=True).start()
            return service

        http = serve()
        def call(method, path, body=None, owner=""):
            data = json.dumps(body).encode() if body else None
            req = urllib.request.Request(f"http://127.0.0.1:{http.server_port}" + path, data=data, method=method,
                                         headers={"Authorization": "Bearer " + owner} if owner else {})
            with urllib.request.urlopen(req, timeout=2) as response:
                return json.load(response)
        profile = {"name":"Test studio","guide_url":"https://studio.example.com/exports/guide.xml",
                   "playlist_url":"https://studio.example.com/exports/channels.m3u","status_url":"https://studio.example.com/api/public/channels",
                   "creator_version":"3.6.2","vault_url":"https://studio.example.com/api/public/vault"}
        key = "be" * 32
        try:
            first = call("POST", "/v1/creators", profile, key)
            second = call("POST", "/v1/creators", profile, key)
            self.assertEqual(first["creator"]["id"], second["creator"]["id"])
            path = "/v1/creators/" + first["creator"]["id"]
            with self.assertRaises(urllib.error.HTTPError) as error:
                call("POST", path + "/heartbeat", {"state": "broadcasting"}, "wrong")
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            call("POST", path + "/heartbeat", {"state":"broadcasting"}, key)
            row = call("GET","/v1/catalog")["creators"][0]
            self.assertEqual(row["state"], "broadcasting")
            self.assertEqual(row["status_url"], profile["status_url"])
            http.shutdown()
            http.server_close()
            http = serve()
            with mock.patch.object(server.time,"time",return_value=row["last_heartbeat"]+86400):
                report = call("GET","/v1/catalog")
                self.assertIs(report["registrations_complete"], True)
                self.assertEqual(report["creators"][0]["state"], "offline")
            call("POST", path + "/heartbeat", {"state":"standby"}, key)
            self.assertEqual(call("GET","/v1/catalog")["creators"][0]["state"],"standby")
            call("DELETE",path,owner=key)
            self.assertFalse(call("GET","/v1/catalog")["creators"])
        finally:
            http.shutdown()
            http.server_close()

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
