import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from hashlib import sha256
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from relay.enrollment import Enrollment, EnrollmentServer, handler
from tests.creator_fixture import app


def public_key(value):
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([value]) * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


class RelayEnrollmentTests(unittest.TestCase):
    def test_unconfirmed_first_publish_cannot_take_every_slot(self):
        slots = [{"origin": f"https://studio{index}.example.com", "remote_port": 18090 + index}
                 for index in range(4)]
        with tempfile.TemporaryDirectory() as root:
            manager = Enrollment(slots, Path(root) / "slots.sqlite", Path(root) / "relay-keys")
            first, second, existing = "a" * 32, "b" * 32, "c" * 32
            self.assertEqual(manager.enroll(first, public_key(1), provisional=True), (200, {"profile": slots[0]}))
            self.assertEqual(manager.enroll(second, public_key(2), provisional=True)[0], 429)
            self.assertEqual(manager.enroll(existing, public_key(3)), (200, {"profile": slots[1]}))
            self.assertEqual(manager.enroll(first, public_key(1), registered_origin=slots[1]["origin"])[0], 200)
            self.assertEqual(manager.enroll(second, public_key(2), provisional=True)[0], 429)
            self.assertEqual(manager.enroll(first, public_key(1), registered_origin=slots[0]["origin"])[0], 200)
            self.assertEqual(manager.enroll(second, public_key(2), provisional=True), (200, {"profile": slots[2]}))

    def test_first_publish_uses_one_identity_for_enrollment_probes_and_registration(self):
        slot = dict(origin="https://relay.tailnet.ts.net/studio/" + "a" * 16, ssh_host="relay.example.com", ssh_user="studio",
                    ssh_port=18088, remote_port=18091, host_key=public_key(1), funnel_host="relay.tailnet.ts.net")
        requests = []

        def directory(method, route, body, key, **_options):
            requests.append((method, route, body, key))
            if route == "/v1/creators/relay":
                if body.get("kind") == "identity":
                    return {"instance": app.RELAY_INSTANCE}
                if body.get("kind") == "sample":
                    return {"sha256": sha256(app.RELAY_MEDIA_SAMPLE).hexdigest()}
                return {"profile": dict(slot)}
            if route == "/v1/creators":
                identity = sha256(f"creator-id:{key}".encode()).hexdigest()[:32]
                return {"creator": {"id": identity}, "api_key": key}
            self.fail(f"Unexpected directory route: {route}")

        connection = mock.Mock()

        def connect(profile):
            self.assertTrue(app.relay_reaches_this_creator(profile.origin))
            self.assertTrue(app.relay_transfers_media(profile.origin, hd=True))
            return connection

        with tempfile.TemporaryDirectory() as root:
            app_dir = Path(root)
            with (mock.patch.multiple(app, DATA_DIR=app_dir, CONFIG_FILE=app_dir / "config.json",
                                      REGISTERED_ORIGIN="", SHARING_STATUS="private"),
                  mock.patch.object(app, "openssh_tool", return_value="ssh-keygen"),
                  mock.patch.object(app.subprocess, "run", return_value=mock.Mock(stdout=public_key(2).encode())),
                  mock.patch.object(app, "start_public_media_server"),
                  mock.patch.object(app, "generate_exports"),
                  mock.patch.object(app, "connect_community_relay", side_effect=connect),
                  mock.patch.object(app, "set_community_relay"),
                  mock.patch.object(app, "directory_request", side_effect=directory)):
                studio = app.publish_studio({"name": "A new Studio", "slug": "new-studio"})
                self.assertEqual(studio["id"], app.config()["creator_id"])
                self.assertEqual(app.config()["public_url"], slot["origin"])
                self.assertTrue(app.config()["managed_relay"])
                self.assertFalse(app.config()["public_tunnel"])
                self.assertEqual(app.community_relay_profile(slot["origin"]).remote_port, slot["remote_port"])
        self.assertEqual([request[1] for request in requests], ["/v1/creators/relay"] * 3 + ["/v1/creators"])
        self.assertEqual(len({request[3] for request in requests}), 1)

    def test_publish_after_upgrade_replaces_an_inherited_lan_url(self):
        inherited = "http://192.168.1.44:8787"
        origin = "https://relay.tailnet.ts.net/studio/" + "a" * 16
        first_profile = {"name": "Sample", "slug": "sample"}
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            configuration = folder / "config.json"
            # Old versions also stored the LAN address in public_url.
            app.atomic_json(configuration, {"public_url": inherited, "creator_profile": first_profile})
            with (mock.patch.multiple(app, DATA_DIR=folder, CONFIG_FILE=configuration, REGISTERED_ORIGIN="",
                                      SHARING_STATUS="idle", SHARING_ERROR=""),
                  mock.patch.object(app, "enrolled_relay_origin", return_value=origin) as enroll,
                  mock.patch.object(app, "community_relay_profile", return_value=mock.Mock(origin=origin)),
                  mock.patch.object(app, "start_public_media_server"),
                  mock.patch.object(app, "connect_community_relay", return_value=mock.Mock()),
                  mock.patch.object(app, "set_community_relay"),
                  mock.patch.object(app, "generate_exports"),
                  mock.patch.object(app, "publish_creator", return_value={"id": "sample"})):
                app.publish_studio({**first_profile, "public_url": inherited})
                enroll.assert_called_once()
                saved = app.config()
                self.assertTrue(saved["managed_relay"])
                self.assertEqual(saved["public_url"], origin)
                self.assertEqual(saved["creator_profile"], first_profile)
        self.assertFalse(app.needs_default_relay("https://custom-media.example.org"))

    def test_linux_update_closes_old_forward_before_exec(self):
        stopping, wake = threading.Event(), threading.Event()
        actions = []

        def record(name):
            self.assertTrue(stopping.is_set())
            self.assertTrue(wake.is_set())
            actions.append(name)

        with (mock.patch.multiple(app, SHUTTING_DOWN=stopping, PUBLISH_CHANGED=wake),
              mock.patch.object(app, "stop_public_tunnel", side_effect=lambda: record("funnel")),
              mock.patch.object(app, "stop_legacy_relay", side_effect=lambda: record("fallback")),
              mock.patch.object(app, "set_community_relay", side_effect=lambda _connection: record("managed")),
              mock.patch.object(app, "stop_ffmpeg_sessions", side_effect=lambda: record("encoders")),
              mock.patch.object(app, "restart_linux_app", side_effect=lambda: record("exec"))):
            app.restart_updated_linux_studio()
        self.assertEqual(actions, ["funnel", "fallback", "managed", "encoders", "exec"])

    def test_two_studios_enroll_and_only_own_slot_can_be_probed(self):
        ids = ("a" * 32, "b" * 32)
        slots = [{"origin": "https://relay.example.com/studio/" + value * 16, "remote_port": 18091 + index}
                 for index, value in enumerate(("a", "b"))]
        with tempfile.TemporaryDirectory() as root:
            database, keys = Path(root) / "slots.sqlite", Path(root) / "relay-authorized-keys"
            manager = Enrollment(slots, database, keys)
            with EnrollmentServer(("127.0.0.1", 0), handler(manager, "b" * 64)) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f"http://127.0.0.1:{server.server_port}/enroll"

                def send(payload, token="b" * 64):
                    request = Request(url, data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + token})
                    try:
                        with urlopen(request, timeout=4) as result:
                            return result.status, json.load(result)
                    except HTTPError as error:
                        with error:
                            return error.code, json.load(error)

                try:
                    first = {"studio": ids[0], "public_key": public_key(1)}
                    second = {"studio": ids[1], "public_key": public_key(2)}
                    self.assertEqual(send(first, "wrong")[0], 403)
                    self.assertEqual(send(first), (200, {"profile": slots[0]}))
                    self.assertEqual(send(second), (200, {"profile": slots[1]}))
                    self.assertEqual(send(first), (200, {"profile": slots[0]}))
                    with ThreadPoolExecutor(max_workers=6) as pool:
                        replies = list(pool.map(send, [first, second] * 6))
                    self.assertEqual(replies, [(200, {"profile": row}) for row in slots * 6])
                    self.assertEqual(send({**first, "public_key": public_key(3)})[0], 409)
                    self.assertEqual(send({"studio": "c" * 32, "public_key": public_key(3)})[0], 503)
                    lines = keys.read_text(encoding="ascii").splitlines()
                    for index, line in enumerate(lines):
                        self.assertIn(f'permitlisten="127.0.0.1:{slots[index]["remote_port"]}"', line)
                        self.assertTrue(line.endswith(public_key(index + 1)))
                    sample = b"a" * (512 * 1024)
                    from email.message import Message

                    class Response(io.BytesIO):
                        headers = Message()

                    Response.headers["Content-Length"] = str(len(sample))
                    Response.headers["Content-Type"] = "application/octet-stream"
                    with mock.patch("relay.enrollment.probe_opener.open", return_value=Response(sample)) as fetch:
                        self.assertEqual(send({"studio": ids[1], "kind": "sample", "nonce": "a" * 32}),
                                         (200, {"sha256": sha256(sample).hexdigest()}))
                        self.assertEqual(fetch.call_args.args[0].full_url,
                                         slots[1]["origin"] + "/api/public/origin?sample=" + "a" * 32)
                finally:
                    server.shutdown()
                    thread.join(timeout=4)
            self.assertEqual(Enrollment(slots, database, keys).enroll(ids[1], public_key(2)), (200, {"profile": slots[1]}))

    def test_unreachable_direct_route_falls_back_with_the_same_pinned_key(self):
        profile = app.CommunityRelayProfile("https://vps.example.com", "vps.example.com", "studio", 22, 18091,
                                            public_key(1), "relay.tailnet.ts.net")
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "community-relay-identity").touch()
            bridge = mock.Mock()
            bridge.getsockname.return_value = ("127.0.0.1", 18182)
            failed = mock.Mock(poll=mock.Mock(return_value=255))
            working = mock.Mock(poll=mock.Mock(return_value=None))
            with (mock.patch.object(app, "DATA_DIR", Path(root)),
                  mock.patch.object(app, "PUBLIC_MEDIA_SERVER", mock.Mock(server_port=8789)),
                  mock.patch.object(app, "RELAY_JOB", mock.Mock()),
                  mock.patch.object(app, "retire_orphaned_relay"),
                  mock.patch.object(app, "openssh_tool", return_value="ssh"),
                  mock.patch.object(app, "start_relay_tls_bridge", return_value=bridge) as tls,
                  mock.patch.object(app, "relay_reaches_this_creator", return_value=True),
                  mock.patch.object(app, "relay_transfers_media", return_value=True) as sample,
                  mock.patch.object(app.subprocess, "Popen", side_effect=[failed, working]) as ssh):
                accepted = app.connect_community_relay(profile)
                self.assertIs(accepted.process, working)
                tls.assert_called_once_with(profile.funnel_host, 8443)
                direct, fallback = [call.args[0] for call in ssh.call_args_list]
                self.assertEqual(direct[-1], profile.ssh_host)
                self.assertIn("HostKeyAlias=" + profile.ssh_host, fallback)
                self.assertEqual(fallback[-1], "127.0.0.1")
                self.assertIn("StrictHostKeyChecking=yes", fallback)
                self.assertIn("127.0.0.1:18091:127.0.0.1:8789", fallback)
                sample.assert_called_once_with(profile.origin, hd=True)

    def test_funnel_is_tried_when_direct_can_connect_but_is_too_slow(self):
        profile = app.CommunityRelayProfile("https://vps.example.com", "vps.example.com", "studio", 22, 18091,
                                            public_key(1), "relay.tailnet.ts.net")
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "community-relay-identity").touch()
            bridge = mock.Mock()
            bridge.getsockname.return_value = ("127.0.0.1", 18182)
            direct, fallback = [mock.Mock(poll=mock.Mock(return_value=None)) for _ in range(2)]
            with (mock.patch.object(app, "DATA_DIR", Path(root)),
                  mock.patch.object(app, "PUBLIC_MEDIA_SERVER", mock.Mock(server_port=8789)),
                  mock.patch.object(app, "RELAY_JOB", mock.Mock()),
                  mock.patch.object(app, "retire_orphaned_relay"),
                  mock.patch.object(app, "openssh_tool", return_value="ssh"),
                  mock.patch.object(app, "start_relay_tls_bridge", return_value=bridge),
                  mock.patch.object(app, "relay_reaches_this_creator", return_value=True),
                  mock.patch.object(app, "relay_transfers_media", side_effect=[False, True]) as sample,
                  mock.patch.object(app, "stop_relay_process") as stop,
                  mock.patch.object(app.subprocess, "Popen", side_effect=[direct, fallback])):
                self.assertIs(app.connect_community_relay(profile).process, fallback)
                stop.assert_called_once_with(direct)
                self.assertEqual(sample.call_args_list, [mock.call(profile.origin, hd=True)] * 2)

    def test_ipv4_only_socket_transports_both_directions_over_verified_tls(self):
        openssl = shutil.which("openssl") or Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if not Path(openssl).is_file():
            self.skipTest("OpenSSL is required to generate a temporary TLS certificate")
        with tempfile.TemporaryDirectory() as root, socket.socket(socket.AF_INET) as gateway:
            cert, key = Path(root) / "cert.pem", Path(root) / "key.pem"
            subprocess.run([str(openssl), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                            "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True, timeout=10)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            gateway.bind(("127.0.0.1", 0))
            gateway.listen(1)
            gateway.settimeout(5)
            verifier = ssl.create_default_context(cafile=str(cert))
            payload = bytes(range(256)) * 1500
            errors = []

            def echo():
                try:
                    connection, _ = gateway.accept()
                    with connection, context.wrap_socket(connection, server_side=True) as peer:
                        peer.settimeout(5)
                        with peer.makefile("rb") as stream:
                            data = stream.read(len(payload))
                        peer.sendall(data[::-1])
                except (OSError, ValueError) as exc:
                    errors.append(exc)

            thread = threading.Thread(target=echo, daemon=True)
            thread.start()
            with mock.patch.object(app.ssl, "create_default_context", return_value=verifier):
                bridge = app.start_relay_tls_bridge("localhost", gateway.getsockname()[1])
                with socket.create_connection(bridge.getsockname(), timeout=5) as studio:
                    studio.sendall(payload)
                    with studio.makefile("rb") as stream:
                        self.assertEqual(stream.read(len(payload)), payload[::-1])
                thread.join(timeout=5)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
