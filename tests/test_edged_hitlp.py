"""HITLP permission overlay: fake HITLP SSE -> edged -> GET /hitlp/events. python3 -m unittest discover -s tests"""
import http.client
import http.server
import json
import os
import queue
import re
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAKPANEL_DATA", tempfile.mkdtemp())
import edged  # noqa: E402

TASK = {"id": "t1", "kind": "action", "action": "deploy frakworks to production", "requester": "smith"}


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class HitlpTest(unittest.TestCase):
    def setUp(self):
        events = self.events = queue.Queue()  # SSE frames the fake HITLP sends

        class FakeHitlp(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                assert self.headers["Authorization"] == "Bearer tok"
                if self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    while True:
                        frame = events.get()
                        if frame is None:
                            return
                        self.wfile.write(frame.encode())
                        self.wfile.flush()
                body = {"tasks": []} if self.path == "/tasks" else TASK if self.path == "/tasks/t1" else None
                data = json.dumps(body).encode()
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        hitlp_srv = serve(FakeHitlp)
        self.addCleanup(hitlp_srv.shutdown)
        self.addCleanup(events.put, None)

        class H(edged.Handler):
            pass
        H.hitlp = edged.Hitlp(f"http://127.0.0.1:{hitlp_srv.server_address[1]}", "tok")
        threading.Thread(target=H.hitlp.listen, daemon=True).start()
        srv = edged.Server(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.port = srv.server_address[1]

    def shell_stream(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/hitlp/events")
        r = c.getresponse()
        self.addCleanup(c.close)
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")

        def next_event():
            name, data = None, None
            while True:
                line = r.fp.readline().decode().rstrip("\n")
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                elif not line and name:
                    return name, data
        return next_event

    def test_show_then_hide_on_decided(self):
        ev = self.shell_stream()
        self.assertEqual(ev()[0], "hide")
        self.events.put('event: task.created\ndata: {"id": "t1"}\n\n')
        name, data = ev()
        self.assertEqual(name, "show")
        self.assertEqual(data["tasks"][0]["summary"], "deploy frakworks to production")
        self.events.put('event: task.decided\ndata: {"id": "t1", "status": "decided"}\n\n')
        self.assertEqual(ev()[0], "hide")

    def test_dismiss_hides(self):
        ev = self.shell_stream()
        ev()
        self.events.put('event: task.created\ndata: {"id": "t1"}\n\n')
        self.assertEqual(ev()[0], "show")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/hitlp/dismiss", json.dumps({"id": "t1"}), {"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 200)
        c.close()
        self.assertEqual(ev()[0], "hide")

    def test_shell_page(self):
        page = edged.SHELL_PAGE
        self.assertIn("new EventSource('/hitlp/events')", page)
        self.assertIn("if (!DISPLAY || DISPLAY === 'panel')", page)  # a virtual Edge on the panel display shows it too
        css = re.search(r"#hitlp\{[^}]*\}", page).group(0)
        w = int(re.search(r"width:(\d+)vw", css).group(1))
        h = int(re.search(r"height:(\d+)vh", css).group(1))
        self.assertGreaterEqual(w * h / 10000, 0.65)
        js = page[page.index("// HITLP permission"):page.index("</script>", page.index("// HITLP permission"))]
        self.assertNotIn("setInterval", js)
        self.assertNotIn("setTimeout", js)


if __name__ == "__main__":
    unittest.main()
