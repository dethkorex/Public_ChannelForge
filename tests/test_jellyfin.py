from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from creator_fixture import app


@contextmanager
def running_server(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield "http://127.0.0.1:" + str(server.server_port)
        finally:
            server.shutdown()
            worker.join()


def request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(url, data=data, method=method, headers={"Content-Type": "application/json"}), timeout=10) as response:
        if response.headers.get_content_type() == "application/json":
            return json.load(response)
        return response.read()


class JellyfinFixture(BaseHTTPRequestHandler):
    library_id, item_id, series_id = "b" * 32, "c" * 32, "d" * 32

    def log_message(self, *_args):
        pass

    def reply(self, payload, content_type="application/json"):
        body = json.dumps(payload).encode() if content_type == "application/json" else payload
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.account = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        account = self.account["Username"]
        if self.path != "/Users/AuthenticateByName" or self.account["Pw"] != "test-password" or account not in ("first", "second"):
            self.send_error(401)
            return
        self.reply({"User": {"Id": ("1" if account == "first" else "2") * 32},
                    "ServerId": "a" * 32, "AccessToken": f"token-for-{account}"})

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith('MediaBrowser Client="ChannelForge"') or "token-for-" not in authorization:
            self.send_error(401)
        elif parsed.path == "/UserViews":
            user = query.get("userId", [""])[0]
            expected = {"1" * 32: "first", "2" * 32: "second"}.get(user, "")
            if not expected or f'Token="token-for-{expected}"' not in authorization:
                self.send_error(401)
                return
            self.reply({"Items": [{"Id": self.library_id, "Name": "TV", "CollectionType": "tvshows"}]})
        elif parsed.path == "/Items":
            if query.get("parentId") != [self.library_id]:
                self.send_error(404)
                return
            offset = int(query["startIndex"][0])
            self.reply({"Items": [{"Id": self.item_id if not offset else "e" * 32, "Name": "The episode",
                                   "Type": "Episode", "RunTimeTicks": 900_000_000, "SeriesId": self.series_id,
                                   "SeriesName": "The show", "ParentIndexNumber": 1, "IndexNumber": offset + 1,
                                   "SeriesPrimaryImageTag": "poster", "ParentBackdropItemId": self.series_id,
                                   "ParentBackdropImageTags": ["backdrop"]}], "TotalRecordCount": 2})
        elif parsed.path == f"/Items/{self.series_id}/Images/Backdrop":
            self.reply(b"scheduled backdrop", "image/jpeg")
        else:
            self.send_error(404)


class JellyfinTests(unittest.TestCase):
    def test_mixed_libraries_save_schedule_and_publish_only_selected_artwork(self):
        with ExitStack() as context:
            root = Path(context.enter_context(tempfile.TemporaryDirectory()))
            for attr, filename in {"CONFIG_FILE": "config.json", "CHANNELS_FILE": "channels.json",
                                   "JELLYFIN_FILE": "jellyfin.json", "BACKUP_DIR": "backups",
                                   "EXPORT_DIR": "exports"}.items():
                context.enter_context(mock.patch.object(app, attr, root / filename))
            context.enter_context(mock.patch.object(app, "queue_channel_publication", return_value=False))
            context.enter_context(mock.patch.object(app, "plex_libraries", return_value=[{"key": "1", "title": "Plex movies", "type": "movie"}]))
            context.enter_context(mock.patch.object(app, "plex_resource_servers", return_value={}))
            context.enter_context(mock.patch.object(app, "local_duration_ms", return_value=90_000))
            app.atomic_json(app.CONFIG_FILE, {"plex_token": "private-plex-key", "plex_url": "http://local-plex", "auto_publish_channels": False})
            jellyfin = context.enter_context(running_server(JellyfinFixture))
            creator = context.enter_context(running_server(app.Handler))
            public = context.enter_context(running_server(app.PublicMediaHandler))
            saved = []
            for account in ("first", "second"):
                saved.append(request(creator + "/api/jellyfin/connections", "POST",
                                     {"url": jellyfin, "username": account, "password": "test-password", "name": account})["connection"])
            self.assertIn("private-plex-key", app.CONFIG_FILE.read_text(encoding="utf-8"))
            self.assertNotIn("test-password", app.JELLYFIN_FILE.read_text(encoding="utf-8"))
            self.assertNotIn("token-for-", json.dumps(app.config(public=True)))
            for name in ("movies", "shows"):
                folder = root / name
                folder.mkdir()
                (folder / f"{name}.mp4").touch()
                request(creator + "/api/local/libraries", "POST", {"name": name, "path": str(folder)})
            libraries = request(creator + "/api/libraries")["libraries"]
            self.assertCountEqual([item["source"] for item in libraries], ["local", "local", "plex", "jellyfin", "jellyfin"])
            media = {}
            for library in libraries:
                if library["source"] == "plex":
                    continue
                section = library["key"]
                media[section] = request(creator + "/api/library/items?" + urlencode({"section": section}))["items"]
            jellyfin_sections = [library["key"] for library in libraries if library["source"] == "jellyfin"]
            first, second = [media[section][0] for section in jellyfin_sections]
            self.assertEqual(first["id"], second["id"])
            self.assertNotEqual(app.media_source_identity(first), app.media_source_identity(second))
            self.assertEqual(len(media[jellyfin_sections[0]]), 2)
            local = [items[0] for section, items in media.items() if section.startswith("local:")]
            channel = request(creator + "/api/channels", "POST", {"name": "Mixed", "number": "81",
                              "section": jellyfin_sections[0], "items": [first, *local, second]})["channel"]
            self.assertEqual([item["source"] for item in channel["items"]], ["jellyfin", "local", "local", "jellyfin"])
            catalog = request(public + "/api/public/channels?guide=extended")
            program = next(row for row in catalog["channels"][0]["programs"] if row["backdrop_url"])
            self.assertNotIn("token-for-", json.dumps(catalog))
            art = urlsplit(program["backdrop_url"])
            self.assertEqual(request(public + art.path + "?" + art.query), b"scheduled backdrop")
            with self.assertRaises(HTTPError) as failure:
                request(public + art.path + "?" + art.query.replace(JellyfinFixture.series_id, "f" * 32))
            self.assertEqual(failure.exception.code, 404)
            with self.assertRaises(HTTPError) as failure:
                request(public + "/api/jellyfin/image?" + urlencode({"id": JellyfinFixture.series_id, "section": jellyfin_sections[0]}))
            self.assertEqual(failure.exception.code, 404)
            request(creator + "/api/jellyfin/connections/" + saved[0]["id"], "DELETE")
            self.assertEqual([row["id"] for row in app.jellyfin_connections()], [saved[1]["id"]])
            self.assertEqual(app.schedule_position(channel, datetime.now(timezone.utc))["item"]["source"], "local")
