"""Allocate bounded Studio relay slots for the authenticated directory service."""

import argparse
import base64
import binascii
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
from threading import BoundedSemaphore
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, Request


def studio_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"ssh-ed25519 [a-zA-Z0-9+/]{68}", value):
        raise ValueError("Expected a Studio Ed25519 public key.")
    try:
        blob = base64.b64decode(value.split()[1], validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid Studio Ed25519 public key.") from exc
    if not blob.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20") or len(blob) != 51:
        raise ValueError("Invalid Studio Ed25519 public key.")
    return value


class Enrollment:
    def __init__(self, slots, database: Path, authorized_keys: Path):
        self.slots = {row["remote_port"]: row for row in slots}
        if (not 1 <= len(slots) <= 100 or len(self.slots) != len(slots)
                or any(type(port) is not int or not 1024 <= port <= 65535 for port in self.slots)
                or len({row["origin"] for row in slots}) != len(slots)):
            raise ValueError("Configure distinct HTTPS origins and loopback ports for the relay slots.")
        for row in slots:
            origin = urlsplit(row["origin"])
            if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                    or origin.query or origin.fragment or (origin.path and not re.fullmatch(r"/studio/[a-f0-9]{16}", origin.path))):
                raise ValueError("Every slot needs an HTTPS origin, optionally with its assigned Studio path.")
        self.database = database
        self.authorized_keys = authorized_keys
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS enrollment (studio TEXT PRIMARY KEY, port INTEGER UNIQUE, key TEXT UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS pending_enrollment (studio TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS pending_key_replacement (studio TEXT PRIMARY KEY, key TEXT UNIQUE, expires INTEGER)")
            self.write_keys(db)

    def write_keys(self, db):
        # A dedicated AuthorizedKeysFile. Never overwrite the VPS admin's keys.
        rows = db.execute("SELECT port, key FROM enrollment ORDER BY port").fetchall()
        db.execute("DELETE FROM pending_key_replacement WHERE expires<=?", (int(time.time()),))
        replacements = db.execute(
            "SELECT enrollment.port, replacement.key, replacement.expires FROM pending_key_replacement replacement "
            "JOIN enrollment USING (studio) ORDER BY enrollment.port"
        ).fetchall()
        if any(port not in self.slots for port, _ in rows):
            raise RuntimeError("Configured relay slots must retain enrolled loopback ports.")
        directory = self.authorized_keys.parent
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False, encoding="ascii") as draft:
            staged = Path(draft.name)
            try:
                for port, key in rows:
                    draft.write(f'command="/usr/sbin/nologin",restrict,port-forwarding,permitlisten="127.0.0.1:{port}" {studio_key(key)}\n')
                for port, key, expires in replacements:
                    deadline = datetime.fromtimestamp(expires, timezone.utc).strftime("%Y%m%d%H%M%SZ")
                    draft.write(f'command="/usr/sbin/nologin",restrict,port-forwarding,permitlisten="127.0.0.1:{port}",expiry-time="{deadline}" {studio_key(key)}\n')
                draft.flush()
                os.fsync(draft.fileno())
                draft.close()
                staged.replace(self.authorized_keys)
            finally:
                staged.unlink(missing_ok=True)

    def enroll(self, studio: str, key: str, *, provisional: bool = False, registered_origin: str = ""):
        if not isinstance(studio, str) or not re.fullmatch(r"[a-f0-9-]{8,64}", studio):
            raise ValueError("Invalid directory Studio identity.")
        key = studio_key(key)
        with closing(sqlite3.connect(self.database, isolation_level=None)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT port, key FROM enrollment WHERE studio=?", (studio,)).fetchone()
            if existing:
                port, current_key = existing
                if current_key != key:
                    return 409, {"error": "This Studio already has a relay key."}
            else:
                if db.execute("SELECT 1 FROM enrollment WHERE key=? UNION ALL SELECT 1 FROM pending_key_replacement WHERE key=? AND expires>?",
                              (key, key, int(time.time()))).fetchone():
                    return 409, {"error": "This relay key already belongs to a Studio."}
                if provisional:
                    count = db.execute("SELECT count(*) FROM pending_enrollment").fetchone()[0]
                    if count >= max(1, len(self.slots) // 4):
                        return 429, {"error": "The relay is at its limit for unconfirmed Studios."}
                    db.execute("INSERT INTO pending_enrollment VALUES (?)", (studio,))
                occupied = {row[0] for row in db.execute("SELECT port FROM enrollment")}
                port = next((port for port in self.slots if port not in occupied), None)
                if port is None:
                    return 503, {"error": "The ChannelForge relay has no free Studio slots."}
                db.execute("INSERT INTO enrollment VALUES (?,?,?)", (studio, port, key))
            if registered_origin == self.slots[port]["origin"]:
                db.execute("DELETE FROM pending_enrollment WHERE studio=?", (studio,))
            # Persist the assignment before enabling its key. Retrying repairs a
            # failed projection, and a second transaction serializes key writes.
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            self.write_keys(db)
        return 200, {"profile": self.slots[port]}

    def replace_key(self, studio, key, *, registered_origin, instance=None):
        key = studio_key(key)
        with closing(sqlite3.connect(self.database)) as db:
            active = db.execute("SELECT port, key FROM enrollment WHERE studio=?", (studio,)).fetchone()
        if not active or self.slots[active[0]]["origin"] != registered_origin:
            return 403, {"error": "Key recovery requires the Studio's registered relay slot."}
        port, current_key = active
        if instance is not None and key != current_key:
            status, proof = self.probe(studio, "identity", secrets.token_hex(6), registered_origin=registered_origin)
            if status != 200 or proof.get("instance") != instance:
                return 412, {"error": "The replacement did not reach this Studio."}
        with closing(sqlite3.connect(self.database, isolation_level=None)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT port, key FROM enrollment WHERE studio=?", (studio,)).fetchone() != active:
                return 409, {"error": "This Studio's relay key changed during recovery."}
            now = int(time.time())
            db.execute("DELETE FROM pending_key_replacement WHERE expires<=?", (now,))
            pending = db.execute("SELECT key FROM pending_key_replacement WHERE studio=?", (studio,)).fetchone()
            if key != current_key:
                if pending and pending[0] != key:
                    return 409, {"error": "A replacement is already pending for this Studio."}
                if instance is not None:
                    if not pending:
                        return 409, {"error": "The replacement has expired. Retry from Studio."}
                    db.execute("UPDATE enrollment SET key=? WHERE studio=?", (key, studio))
                    db.execute("DELETE FROM pending_key_replacement WHERE studio=?", (studio,))
                elif not pending:
                    taken = db.execute("SELECT 1 FROM enrollment WHERE key=? UNION ALL SELECT 1 FROM pending_key_replacement WHERE key=?",
                                       (key, key)).fetchone()
                    if taken:
                        return 409, {"error": "That key is assigned to another Studio."}
                    db.execute("INSERT INTO pending_key_replacement VALUES (?,?,?)", (studio, key, now + 300))
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            self.write_keys(db)
        return 200, {"profile": self.slots[port]}

    def probe(self, studio, kind, nonce, *, registered_origin: str = ""):
        if not isinstance(nonce, str) or not (
            kind == "identity" and re.fullmatch(r"[a-f0-9]{12}", nonce)
            or kind == "sample" and re.fullmatch(r"[A-Za-z0-9_-]{32}", nonce)
        ):
            raise ValueError("Invalid probe.")
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT port FROM enrollment WHERE studio=?", (studio,)).fetchone()
        if not row:
            return 404, {"error": "This Studio has no relay slot."}
        param = "check" if kind == "identity" else "sample"
        origin = self.slots[row[0]]["origin"]
        # Clear reservations only for the verified, published origin assigned to this Studio.
        if registered_origin == origin:
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("DELETE FROM pending_enrollment WHERE studio=?", (studio,))
        request = Request(f"{origin}/api/public/origin?{param}={nonce}", headers={"Cache-Control": "no-cache"})
        start = time.monotonic()
        with probe_opener.open(request, timeout=1 if kind == "sample" else 3) as response:
            if kind == "identity":
                result = json.loads(response.read(513))
                instance = result.get("instance") if isinstance(result, dict) else None
                if not isinstance(instance, str) or not re.fullmatch(r"[a-f0-9]{40}", instance):
                    raise ValueError("Invalid Studio origin response.")
                return 200, {"instance": instance}
            size = int(response.headers.get("Content-Length", "0"))
            if response.headers.get_content_type() != "application/octet-stream" or size not in {128 * 1024, 512 * 1024}:
                raise ValueError("Invalid Studio bandwidth sample.")
            digest = sha256()
            remaining = size
            while remaining and time.monotonic() - start <= 1:
                data = response.read1(min(remaining, 16 * 1024))
                if not data:
                    break
                remaining -= len(data)
                digest.update(data)
            if remaining or time.monotonic() - start > 1:
                return 503, {"error": "The relay could not transfer the Studio test sample in time."}
            return 200, {"sha256": digest.hexdigest()}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


probe_opener = build_opener(NoRedirect())


class EnrollmentServer(ThreadingHTTPServer):
    def __init__(self, address, handler_class):
        self.in_flight = BoundedSemaphore(16)
        super().__init__(address, handler_class)

    def process_request(self, request, address):
        if not self.in_flight.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.in_flight.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.in_flight.release()


def handler(enrollment, token: str):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(4)

        def log_message(self, *_args):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/enroll":
                return self.reply(404, {"error": "Not found"})
            provided = self.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(provided, ("Bearer " + token).encode()):
                return self.reply(403, {"error": "Relay enrollment requires directory authorization."})
            size = self.headers.get("Content-Length", "")
            if not re.fullmatch(r"[1-9][0-9]{0,2}", size):
                return self.reply(400, {"error": "Invalid enrollment request size."})
            try:
                request = json.loads(self.rfile.read(int(size)))
                if not isinstance(request, dict):
                    raise ValueError("Invalid enrollment request.")
                studio = request.get("studio")
                if not isinstance(studio, str) or not re.fullmatch(r"[a-f0-9-]{8,64}", studio):
                    raise ValueError("Invalid directory Studio identity.")
                kind = request.get("kind")
                if kind in ("stage_key", "commit_key"):
                    instance = request.get("instance") if kind == "commit_key" else None
                    if kind == "commit_key" and (not isinstance(instance, str) or not re.fullmatch(r"[a-f0-9]{40}", instance)):
                        raise ValueError("Invalid Studio instance proof.")
                    status, result = enrollment.replace_key(studio, request.get("public_key"),
                                                            registered_origin=request.get("registered_origin", ""),
                                                            instance=instance)
                elif "kind" in request:
                    status, result = enrollment.probe(studio, request.get("kind"), request.get("nonce"),
                                                      registered_origin=request.get("registered_origin", ""))
                else:
                    status, result = enrollment.enroll(studio, request.get("public_key"),
                                                        provisional=request.get("provisional") is True,
                                                        registered_origin=request.get("registered_origin", ""))
            except (ValueError, TimeoutError):
                status, result = 400, {"error": "Invalid Studio enrollment request."}
            except (OSError, sqlite3.Error):
                status, result = 503, {"error": "Relay enrollment is temporarily unavailable."}
            self.reply(status, result)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slots", type=Path, help="Operator-provisioned Studio profiles in JSON")
    parser.add_argument("database", type=Path)
    parser.add_argument("authorized_keys", type=Path, help="Dedicated relay AuthorizedKeysFile")
    parser.add_argument("--port", type=int, default=18089)
    args = parser.parse_args()
    token = os.environ.get("CHANNELFORGE_RELAY_ENROLL_TOKEN", "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        parser.error("Set CHANNELFORGE_RELAY_ENROLL_TOKEN to a random 32-byte hexadecimal secret.")
    enrollment = Enrollment(json.loads(args.slots.read_bytes()), args.database, args.authorized_keys)
    with EnrollmentServer(("127.0.0.1", args.port), handler(enrollment, token)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
