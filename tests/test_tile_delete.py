"""POST /tiles/delete on edged's Handler: python3 -m unittest discover -s tests"""
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
import edgerelay  # noqa: E402
import mcp_tiles  # noqa: E402

TILE = {"url": "https://example.lan/tile", "title": "my tile"}
LOCAL = {"url": "https://example.lan/local", "title": "local"}


class Layout:
    def get(self):
        return [TILE["url"], "", ""]


class TileDeleteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.relay = edgerelay.RelayServer(lambda m: None, os.path.join(tmp.name, "edge_relays.json"))
        self.relay.titles.update({"air": "Air", "away": "Away"})
        self.relay.clients["air"] = object()
        self.registry = mcp_tiles.TileRegistry(os.path.join(tmp.name, "mcp_tiles.json"))
        self.registry.register(**TILE)

        class H(edged.Handler):
            pass
        H.layout, H.relay, H.local_tiles = Layout(), self.relay, [{"url": "", "title": "clock"}, LOCAL]
        H.mcp = mcp_tiles.McpServer(self.registry, "test")
        srv = edged.Server(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.port = srv.server_address[1]

    def req(self, method, url, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, url, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def delete(self, url):
        return self.req("POST", "/tiles/delete", {"url": url})[0]

    def tiles(self):
        return {t["url"]: t for t in self.req("GET", "/tiles")[1]["tiles"]}

    def test_deletable_flags(self):
        t = self.tiles()
        self.assertFalse(t[""]["deletable"])
        self.assertFalse(t[LOCAL["url"]]["deletable"])
        self.assertTrue(t[TILE["url"]]["deletable"])
        self.assertFalse(t["relay://air/"]["deletable"])
        self.assertTrue(t["relay://away/"]["deletable"])

    def test_deletes_registered_tile(self):
        self.assertEqual(self.delete(TILE["url"]), 200)
        self.assertNotIn(TILE["url"], self.tiles())
        self.assertEqual(self.registry.list(), [])
        out = self.registry  # reload from disk: gone from mcp_tiles.json too
        self.assertEqual(mcp_tiles.TileRegistry(out.path).list(), [])
        self.assertEqual(self.req("GET", "/tiles")[1]["slots"][0], TILE["url"])  # slot keeps its URL

    def test_refuses_unknown_and_fixed(self):
        self.assertEqual(self.delete("https://nope.lan/"), 404)
        self.assertEqual(self.delete("relay://ghost/"), 404)
        self.assertEqual(self.delete(""), 409)
        self.assertEqual(self.delete(LOCAL["url"]), 409)
        self.assertEqual(self.req("POST", "/tiles/delete", {})[0], 400)

    def test_relay_entries(self):
        self.assertEqual(self.delete("relay://air/"), 409)
        self.assertEqual(self.delete("relay://away/"), 200)
        self.assertNotIn("relay://away/", self.tiles())
        self.assertIn("relay://air/", self.tiles())


if __name__ == "__main__":
    unittest.main()
