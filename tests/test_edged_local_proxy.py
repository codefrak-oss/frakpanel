import http.server
import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import edged  # noqa: E402


class Echo(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self._reply(b"GET " + self.path.encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._reply(b"POST " + self.path.encode() + b" " + self.rfile.read(n))

    def _reply(self, data):
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def handler(client: str, path: str = "/", body: bytes = b"") -> edged.Handler:
    h = edged.Handler.__new__(edged.Handler)
    h.client_address = (client, 50000)
    h.path = path
    h.headers = {"Content-Length": str(len(body))} if body else {}
    h.rfile = io.BytesIO(body)
    h.sent = []
    h.json = lambda code, obj: h.sent.append((code, obj))
    return h


class TestResolveLocal(unittest.TestCase):
    def test_remote_viewer_gets_hub_path(self):
        h = handler("192.168.1.50")
        self.assertEqual(h.resolve("http://127.0.0.1:7785/"), "/local/7785/")
        self.assertEqual(h.resolve("http://localhost:8096/web/index.html?a=1"), "/local/8096/web/index.html?a=1")
        self.assertEqual(h.resolve("http://grafana.lan:3000/"), "http://grafana.lan:3000/")

    def test_local_viewer_gets_url_verbatim(self):
        h = handler("127.0.0.1")
        self.assertEqual(h.resolve("http://127.0.0.1:7785/"), "http://127.0.0.1:7785/")


class TestProxyLocal(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Echo)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def run_proxy(self, method, path, body=b""):
        h = handler("192.168.1.50", path, body)
        out = {}
        h.send_response = lambda code: out.setdefault("code", code)
        h.send_header = lambda k, v: None
        h.end_headers = lambda: None
        h.wfile = io.BytesIO()
        h.proxy_local(method)
        return out.get("code"), h.wfile.getvalue(), h.sent

    def test_get_and_post_forwarded(self):
        code, data, _ = self.run_proxy("GET", f"/local/{self.port}/x?y=1")
        self.assertEqual((code, data), (200, b"GET /x?y=1"))
        code, data, _ = self.run_proxy("POST", f"/local/{self.port}/p", b"hi")
        self.assertEqual((code, data), (200, b"POST /p hi"))

    def test_unreachable_port_is_502(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        _, _, sent = self.run_proxy("GET", f"/local/{port}/")
        self.assertEqual(sent[0][0], 502)
        self.assertIn("error", sent[0][1])

    def test_bad_port_refused(self):
        _, _, sent = self.run_proxy("GET", "/local/evil.com:80/")
        self.assertEqual(sent[0][0], 400)


if __name__ == "__main__":
    unittest.main()
