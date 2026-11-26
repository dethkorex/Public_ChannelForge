from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import unittest

from scripts.announce_release import messages, post_message


class AnnouncementTests(unittest.TestCase):
    def test_routes_all_notes_to_discord_without_ping_or_truncation(self):
        notes = "\n".join(f"- **Change {index:02d}.** " + "Text for the public release. " * 6 for index in range(24))
        built = messages("v4.0.0", "dethkorex/Public_ChannelForge", "# ChannelForge\n\n## Playback\n" + notes)
        self.assertGreater(len(built), 1)
        self.assertIn("**Playback**", built[0])
        for index in range(24):
            self.assertIn(f"**Change {index:02d}.**", "\n".join(built))
        for message in built:
            self.assertLessEqual(len(message.encode("utf-16-le")) // 2, 2000)
        self.assertIn("/releases/tag/v4.0.0", built[-1])
        with self.assertRaises(ValueError):
            messages("v4.0.0-alpha.1", "dethkorex/Public_ChannelForge", notes)

        posts = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                posts.append((self.path, payload))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"id":"confirmed"}')

            def log_message(self, *_args):
                pass

        with HTTPServer(("127.0.0.1", 0), Receiver) as receiver:
            worker = threading.Thread(target=receiver.serve_forever, daemon=True)
            worker.start()
            try:
                post_message(f"http://127.0.0.1:{receiver.server_port}/webhook", built[-1])
            finally:
                receiver.shutdown()
                worker.join()
        path, payload = posts[0]
        self.assertEqual(path, "/webhook?wait=true")
        self.assertEqual(payload["content"], built[-1])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
