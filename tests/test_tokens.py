import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from agentcontrol import settings, tokens


class TrackerCommand(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def which(self, available):
        return mock.patch.object(tokens.shutil, "which", lambda n: f"/usr/bin/{n}" if n in available else None)

    def test_setting_wins(self):
        settings.save(settings.load() | {"tokentracker": "/opt/tt --flag 'a b'"})
        with self.which({"tokentracker", "npx"}):
            self.assertEqual(tokens.tracker_argv(), ["/opt/tt", "--flag", "a b"])

    def test_blank_setting_is_automatic(self):
        settings.save(settings.load() | {"tokentracker": "   "})
        with self.which({"tokentracker"}):
            self.assertEqual(tokens.tracker_argv(), ["/usr/bin/tokentracker"])

    def test_npx_fallback_and_nothing(self):
        with self.which({"npx"}):
            self.assertEqual(tokens.tracker_argv(), ["/usr/bin/npx", "-y", "tokentracker-cli@latest"])
        with self.which(set()):
            self.assertIsNone(tokens.tracker_argv())


class Server(unittest.TestCase):
    def serve(self, body, status=200):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_real_server(self):
        self.assertTrue(tokens.running_server(self.serve(json.dumps({"serverVersion": "1.2"}))))

    def test_other_service_on_port(self):
        self.assertFalse(tokens.running_server(self.serve(json.dumps({"hello": 1}))))
        self.assertFalse(tokens.running_server(self.serve("<html>not json</html>")))
        self.assertFalse(tokens.running_server(self.serve("[1, 2]")))
        self.assertFalse(tokens.running_server(self.serve("{}", status=500)))

    def test_nothing_listening(self):
        self.assertFalse(tokens.running_server("http://127.0.0.1:1"))


if __name__ == "__main__":
    unittest.main()
