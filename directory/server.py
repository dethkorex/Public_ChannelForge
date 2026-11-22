"""ChannelForge Creator Directory reference service.

Creators explicitly publish only their public guide/playlist endpoints. Plex tokens,
local paths, channel databases, and media never pass through this service.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path(os.environ.get("CHANNELFORGE_DIRECTORY_DB", "directory.db"))
HOST = os.environ.get("CHANNELFORGE_DIRECTORY_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHANNELFORGE_DIRECTORY_PORT", "8790"))


def connection() -> sqlite3.Connection:
    database = sqlite3.connect(DB_PATH)
    database.row_factory = sqlite3.Row
    database.execute("""CREATE TABLE IF NOT EXISTS creators (
        id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        description TEXT NOT NULL, guide_url TEXT NOT NULL, playlist_url TEXT NOT NULL,
        logo_url TEXT NOT NULL, key_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        updated_at INTEGER NOT NULL
    )""")
    return database


def clean_url(value: object, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    parsed = urllib.parse.urlsplit(text)
    development = os.environ.get("CHANNELFORGE_DIRECTORY_ALLOW_HTTP") == "1"
    if parsed.scheme not in ({"http", "https"} if development else {"https"}) or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Published URLs must be public HTTPS addresses.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))[:1000]


def clean_payload(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()[:80]
    if not name:
        raise ValueError("Creator name is required.")
    slug = re.sub(r"[^a-z0-9]+", "-", str(payload.get("slug") or name).lower()).strip("-")[:60]
    if len(slug) < 3:
        raise ValueError("Creator slug must contain at least three letters or numbers.")
    return {
        "slug": slug, "name": name,
        "description": str(payload.get("description", "")).strip()[:400],
        "guide_url": clean_url(payload.get("guide_url")),
        "playlist_url": clean_url(payload.get("playlist_url")),
        "logo_url": clean_url(payload.get("logo_url"), True),
    }


def public_creator(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "slug", "name", "description", "guide_url", "playlist_url", "logo_url", "updated_at")}


class Handler(BaseHTTPRequestHandler):
    server_version = "ChannelForgeDirectory/1.0"

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=30" if status == 200 else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64_000:
            raise ValueError("Request is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected an object.")
        return value

    def authorized(self, row: sqlite3.Row) -> bool:
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(supplied) and secrets.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), row["key_hash"])

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                return self.send_json(200, {"ok": True, "service": "ChannelForge Creator Directory"})
            if path == "/v1/catalog":
                with connection() as database:
                    rows = database.execute("SELECT * FROM creators WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
                return self.send_json(200, {"version": 1, "generated_at": int(time.time()), "creators": [public_creator(row) for row in rows]})
            if path.startswith("/v1/creators/"):
                slug = path.rsplit("/", 1)[-1]
                with connection() as database:
                    row = database.execute("SELECT * FROM creators WHERE slug=? AND active=1", (slug,)).fetchone()
                return self.send_json(200, {"creator": public_creator(row)}) if row else self.send_json(404, {"error": "Creator not found"})
            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        try:
            if urllib.parse.urlsplit(self.path).path != "/v1/creators":
                return self.send_json(404, {"error": "Not found"})
            value = clean_payload(self.body())
            creator_id, api_key = secrets.token_hex(12), secrets.token_urlsafe(32)
            with connection() as database:
                database.execute("INSERT INTO creators VALUES (?,?,?,?,?,?,?,?,1,?)", (
                    creator_id, value["slug"], value["name"], value["description"], value["guide_url"],
                    value["playlist_url"], value["logo_url"], hashlib.sha256(api_key.encode()).hexdigest(), int(time.time()),
                ))
            self.send_json(201, {"creator": {"id": creator_id, **value}, "api_key": api_key})
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "That creator name is already registered."})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_PUT(self) -> None:
        self._update(False)

    def do_DELETE(self) -> None:
        self._update(True)

    def _update(self, deleting: bool) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith("/v1/creators/"):
                return self.send_json(404, {"error": "Not found"})
            creator_id = path.rsplit("/", 1)[-1]
            with connection() as database:
                row = database.execute("SELECT * FROM creators WHERE id=?", (creator_id,)).fetchone()
                if not row or not self.authorized(row):
                    return self.send_json(403, {"error": "Invalid creator key"})
                if deleting:
                    database.execute("UPDATE creators SET active=0,updated_at=? WHERE id=?", (int(time.time()), creator_id))
                    return self.send_json(200, {"ok": True})
                value = clean_payload(self.body())
                database.execute("UPDATE creators SET slug=?,name=?,description=?,guide_url=?,playlist_url=?,logo_url=?,active=1,updated_at=? WHERE id=?", (
                    value["slug"], value["name"], value["description"], value["guide_url"], value["playlist_url"], value["logo_url"], int(time.time()), creator_id,
                ))
            self.send_json(200, {"creator": {"id": creator_id, **value}})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})


def main() -> None:
    with connection():
        pass
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ChannelForge Creator Directory listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
