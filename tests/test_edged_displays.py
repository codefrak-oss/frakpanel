"""Per-display layouts, the panel's back-compat, and the relay proxy: python3 -m unittest discover -s tests"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAKPANEL_DATA", tempfile.mkdtemp())
import edged  # noqa: E402
import mcp_tiles  # noqa: E402

A, B = "http://a.example/", "http://b.example/"


class Relay:
    """A stand-in RelayServer: one relay "air" on a given 127.0.0.1 port."""

    def __init__(self, port=0):
        self.port = port

    def url_for(self, name, path):
        return f"http://127.0.0.1:{self.port}/{path.lstrip('/')}" if name == "air" else None

    def tiles(self):
        return [{"url": "relay://air/", "title": "Air", "laptop": "air", "online": True}]

    def status(self):
        return {"air": {"port": self.port}}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        for attr, val in (("LAYOUT_PATH", os.path.join(tmp.name, "edge_layout.json")),
                          ("LAYOUTS_DIR", os.path.join(tmp.name, "edge_layouts"))):
            self.addCleanup(setattr, edged, attr, getattr(edged, attr))
            setattr(edged, attr, val)

    def serve(self, relay=None):
        class H(edged.Handler):
            pass
        H.layout, H.relay = edged.Layout(), relay or Relay()
        H.local_tiles = [{"url": "", "title": "clock"}]
        H.mcp = mcp_tiles.McpServer(mcp_tiles.TileRegistry(os.path.join(self.dir, "mcp.json")), "test")
        srv = edged.Server(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.port = srv.server_address[1]
        return H

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json"} if body is not None else {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (json.loads(data) if r.getheader("Content-Type") == "application/json" else data)


class DisplayIsolationTest(Base):
    def test_layout_per_display(self):
        self.serve()
        self.assertEqual(self.req("POST", "/layout?display=work", {"slots": [A]})[0], 200)
        self.req("POST", "/layout", {"slots": ["", B], "display": "home"})
        self.assertEqual(self.req("GET", "/layout?display=work")[1]["slots"], [A, "", ""])
        self.assertEqual(self.req("GET", "/layout?display=home")[1]["slots"], ["", B, ""])
        self.assertEqual(self.req("GET", "/layout")[1]["slots"], ["", "", ""])
        self.assertFalse(os.path.exists(edged.LAYOUT_PATH))

    def test_slot_per_display(self):
        self.serve()
        self.req("POST", "/slot?display=work", {"slot": 2, "url": A})
        self.req("POST", "/slot", {"slot": 0, "url": B})
        self.assertEqual(self.req("GET", "/tiles?display=work")[1]["slots"], ["", "", A])
        self.assertEqual(self.req("GET", "/tiles")[1]["slots"], [B, "", ""])
        self.assertEqual(self.req("GET", "/displays")[1],
                         {"displays": {"panel": [B, "", ""], "work": ["", "", A]}})

    def test_same_tiles_for_every_display(self):
        self.serve()
        self.req("POST", "/slot?display=work", {"slot": 0, "url": A})
        tiles = {d: self.req("GET", "/tiles" + q)[1]["tiles"] for d, q in (("panel", ""), ("work", "?display=work"))}
        self.assertEqual(tiles["panel"], tiles["work"])

    def test_bad_display(self):
        self.serve()
        self.assertEqual(self.req("GET", "/layout?display=../x")[0], 400)
        self.assertEqual(self.req("POST", "/slot?display=a%20b", {"slot": 0, "url": A})[0], 400)

    def test_survives_restart(self):
        self.serve()
        self.req("POST", "/slot?display=work", {"slot": 1, "url": A})
        layout = edged.Layout()
        self.assertEqual(layout.get("work"), ["", A, ""])
        self.assertEqual(layout.displays(), ["panel", "work"])


class PanelBackCompatTest(Base):
    def test_existing_layout_is_panel(self):
        with open(edged.LAYOUT_PATH, "w", encoding="utf-8") as f:
            json.dump({"slots": [A, B, ""]}, f)
        self.serve()
        self.assertEqual(self.req("GET", "/layout")[1]["slots"], [A, B, ""])
        self.assertEqual(self.req("GET", "/layout?display=panel")[1]["slots"], [A, B, ""])
        self.assertEqual(self.req("GET", "/layout?display=work")[1]["slots"], ["", "", ""])

    def test_post_without_display_writes_edge_layout(self):
        self.serve()
        self.req("POST", "/layout", {"slots": [B]})
        with open(edged.LAYOUT_PATH, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"slots": [B, "", ""]})
        self.assertFalse(os.path.exists(edged.LAYOUTS_DIR))


class RelayTest(Base):
    def handler(self, client):
        h = edged.Handler.__new__(edged.Handler)
        h.relay, h.client_address = Relay(9999), (client, 1234)
        h.headers = {"Host": "nuc.local:7781"}
        return h

    def test_resolve_by_client(self):
        self.assertEqual(self.handler("127.0.0.1").resolve("relay://air/x"), "http://127.0.0.1:9999/x")
        self.assertEqual(self.handler("::1").resolve("relay://air/x"), "http://127.0.0.1:9999/x")
        self.assertEqual(self.handler("192.168.1.20").resolve("relay://air/x"), "http://nuc.local:9999/x")
        self.assertEqual(self.handler("::ffff:100.64.0.5").resolve("relay://air/"), "http://nuc.local:9999/")
        self.assertEqual(self.handler("192.168.1.20").resolve("relay://gone/"), "/home?waiting=gone")

    def test_two_displays_one_relay(self):
        self.serve()
        for d in ("panel", "work"):
            self.req("POST", f"/slot?display={d}", {"slot": 0, "url": "relay://air/"})
        for d in ("panel", "work"):
            self.assertEqual(self.req("GET", f"/layout?display={d}")[1]["slots"][0], "http://127.0.0.1:0/")


if __name__ == "__main__":
    unittest.main()
