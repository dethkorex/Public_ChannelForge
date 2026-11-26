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
from contextlib import closing, contextmanager
from typing import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB_PATH = Path(os.environ.get("CHANNELFORGE_DIRECTORY_DB", "directory.db"))
HOST = os.environ.get("CHANNELFORGE_DIRECTORY_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHANNELFORGE_DIRECTORY_PORT", "8790"))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(DB_PATH)) as database, database:
        database.row_factory = sqlite3.Row
        database.execute("""CREATE TABLE IF NOT EXISTS creators (
            id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL, guide_url TEXT NOT NULL, playlist_url TEXT NOT NULL,
            vault_url TEXT NOT NULL DEFAULT '', logo_url TEXT NOT NULL, key_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL
        )""")
        columns = {row[1] for row in database.execute("PRAGMA table_info(creators)")}
        for column in ("vault_url", "status_url", "creator_version", "state", "last_heartbeat"):
            if column not in columns:
                definition = "INTEGER NOT NULL DEFAULT 0" if column == "last_heartbeat" else "TEXT NOT NULL DEFAULT ''"
                database.execute(f"ALTER TABLE creators ADD COLUMN {column} {definition}")
        yield database


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
        "vault_url": clean_url(payload.get("vault_url")),
        "logo_url": clean_url(payload.get("logo_url"), True),
        "status_url": clean_url(payload.get("status_url"), True),
        "creator_version": str(payload.get("creator_version") or "")[:32],
    }


def public_creator(row: sqlite3.Row) -> dict:
    data = {key: row[key] for key in ("id", "slug", "name", "description", "guide_url", "playlist_url",
                                        "vault_url", "logo_url", "status_url", "creator_version", "updated_at", "last_heartbeat")}
    grace = min(86400, max(60, int(os.environ.get("CHANNELFORGE_DIRECTORY_GRACE_SECONDS", "360"))))
    age = int(time.time()) - (row["last_heartbeat"] or row["updated_at"])
    state = row["state"] or "standby"
    # An old Creator never negotiated heartbeats. Its media endpoint remains
    # the authority for playback health.
    if row["state"] or row["last_heartbeat"]:
        if age >= grace:
            state = "offline"
        elif age >= grace // 2:
            state = "reconnecting"
    return {**data, "state": state, "online": state != "offline"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ChannelForgeDirectory/1.0"

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=30" if status == 200 and self.command == "GET" else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 64_000:
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
                return self.send_json(200, {"version": 1, "registrations_complete": True, "generated_at": int(time.time()), "creators": [public_creator(row) for row in rows]})
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
            path = urllib.parse.urlsplit(self.path).path
            heartbeat = re.fullmatch(r"/v1/creators/([a-f0-9-]+)/heartbeat", path)
            if heartbeat:
                state = self.body().get("state")
                if state not in {"starting", "standby", "broadcasting", "overloaded", "reconnecting"}:
                    return self.send_json(400, {"error": "Unknown studio state."})
                with connection() as db:
                    row = db.execute("SELECT * FROM creators WHERE id=? AND active=1", (heartbeat[1],)).fetchone()
                    if not row or not self.authorized(row):
                        return self.send_json(403, {"error": "Invalid creator key"})
                    db.execute("UPDATE creators SET last_heartbeat=?,state=? WHERE id=?", (int(time.time()), state, heartbeat[1]))
                return self.send_json(200, {"ok": True})
            if path != "/v1/creators":
                return self.send_json(404, {"error": "Not found"})
            value = clean_payload(self.body())
            supplied = self.headers.get("Authorization", "")
            if supplied and not re.fullmatch(r"Bearer [a-f0-9]{64}", supplied):
                return self.send_json(400, {"error": "Invalid registration key."})
            api_key = supplied[7:] if supplied else secrets.token_urlsafe(32)
            creator_id = hashlib.sha256(("creator-id:" + api_key).encode()).hexdigest()[:32] if supplied else secrets.token_hex(12)
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            with connection() as database:
                database.execute("""INSERT INTO creators
                    (id,slug,name,description,guide_url,playlist_url,vault_url,logo_url,status_url,creator_version,key_hash,active,updated_at,state)
                    VALUES (:id,:slug,:name,:description,:guide_url,:playlist_url,:vault_url,:logo_url,:status_url,:creator_version,:key_hash,1,:now,:state)
                    ON CONFLICT(id) DO UPDATE SET active=1,slug=excluded.slug,name=excluded.name,description=excluded.description,
                      guide_url=excluded.guide_url,playlist_url=excluded.playlist_url,vault_url=excluded.vault_url,
                      status_url=excluded.status_url,creator_version=excluded.creator_version,logo_url=excluded.logo_url,
                      updated_at=excluded.updated_at WHERE creators.key_hash=excluded.key_hash""",
                    {**value, "id": creator_id, "key_hash": key_hash, "now": int(time.time()), "state":"starting" if supplied else ""})
                row = database.execute("SELECT * FROM creators WHERE id=?", (creator_id,)).fetchone()
            self.send_json(201, {"creator": public_creator(row), "heartbeat_supported": True, "api_key": api_key})
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
                    result = {"ok": True}
                else:
                    value = clean_payload(self.body())
                    database.execute("""UPDATE creators SET slug=:slug,name=:name,description=:description,
                        guide_url=:guide_url,playlist_url=:playlist_url,vault_url=:vault_url,logo_url=:logo_url,
                        status_url=:status_url,creator_version=:creator_version,active=1,updated_at=:now WHERE id=:id""",
                        {**value,"now":int(time.time()),"id":creator_id})
                    result = {"creator": {"id":creator_id, **value}, "heartbeat_supported":True}
            self.send_json(200, result)
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
