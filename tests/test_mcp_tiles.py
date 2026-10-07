"""Tests for the MCP tile server: python3 -m unittest discover -s tests"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAKPANEL_DATA", tempfile.mkdtemp())
import mcp_tiles  # noqa: E402

TILE = {"url": "https://engine.k8s.lund/frakpanel/tiles/ai-accounts", "title": "AI accounts"}


class McpTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "mcp_tiles.json")
        self.server = self.make()
        self.ids = 0

    def tearDown(self):
        self.dir.cleanup()

    def make(self):
        return mcp_tiles.McpServer(mcp_tiles.TileRegistry(self.path), "test")

    def rpc(self, method, params=None, server=None):
        self.ids += 1
        out = (server or self.server).handle(json.dumps(
            {"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params or {}}).encode())
        self.assertEqual(out["id"], self.ids)
        return out

    def call(self, name, server=None, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args}, server)["result"]
        return res, json.loads(res["content"][0]["text"]) if not res["isError"] else res["content"][0]["text"]

    def test_initialize_and_tools_list(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        self.assertIsNone(self.server.handle(b'{"jsonrpc":"2.0","method":"notifications/initialized"}'))
        names = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"frakpanel_guide", "register_tile", "list_tiles", "remove_tile"})
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.server.handle(b"{bad")["error"]["code"], -32700)

    def test_register_list_remove(self):
        res, _ = self.call("register_tile", **TILE)
        self.assertFalse(res["isError"])
        self.assertEqual(self.call("list_tiles")[1], {"tiles": [TILE]})
        self.call("register_tile", url=TILE["url"], title="Renamed")
        self.assertEqual(self.call("list_tiles")[1]["tiles"], [{"url": TILE["url"], "title": "Renamed"}])
        res, _ = self.call("remove_tile", url=TILE["url"])
        self.assertFalse(res["isError"])
        self.assertEqual(self.call("list_tiles")[1], {"tiles": []})
        self.assertTrue(self.call("remove_tile", url=TILE["url"])[0]["isError"])

    def test_guide(self):
        tool = next(t for t in self.rpc("tools/list")["result"]["tools"] if t["name"] == "frakpanel_guide")
        self.assertIn("first", tool["description"])
        self.assertFalse(tool["inputSchema"].get("required"))
        res = self.rpc("tools/call", {"name": "frakpanel_guide"})["result"]
        self.assertFalse(res["isError"])
        text = res["content"][0]["text"]
        with open(os.path.join(mcp_tiles.HERE, "TILES.md"), encoding="utf-8") as f:
            self.assertIn(f.read(), text)
        self.assertIn("examples/self_hosted_tile.py", text)
        self.assertIn("def ", text.split("# examples/self_hosted_tile.py")[1])
        self.assertIn("X-Frame-Options", text)

    def test_guide_missing(self):
        old = mcp_tiles.HERE
        mcp_tiles.HERE = self.dir.name
        try:
            res = self.rpc("tools/call", {"name": "frakpanel_guide", "arguments": {}})["result"]
        finally:
            mcp_tiles.HERE = old
        self.assertTrue(res["isError"])

    def test_persists_across_restart(self):
        self.call("register_tile", **TILE)
        restarted = self.make()
        self.assertEqual(self.call("list_tiles", server=restarted)[1], {"tiles": [TILE]})

    def test_invalid_input_registers_nothing(self):
        self.call("register_tile", **TILE)
        with open(self.path, "rb") as f:
            before = f.read()
        for args in ({"url": "ftp://x/y", "title": "x"}, {"url": "engine.k8s.lund/x", "title": "x"},
                     {"url": "https://x/", "title": ""}, {"url": "https://x/", "title": "   "},
                     {"url": "https://x/"}, {"title": "x"}):
            res, _ = self.call("register_tile", **args)
            self.assertTrue(res["isError"], args)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.call("list_tiles")[1], {"tiles": [TILE]})


class EdgedHttpTest(unittest.TestCase):
    """POST /mcp then GET /tiles on edged's own Handler, no restart."""

    def test_register_then_tiles(self):
        import edged
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "mcp_tiles.json")

        class Relay:
            def tiles(self):
                return []

        class Layout:
            def get(self):
                return ["", "", ""]

        class H(edged.Handler):
            pass
        H.layout, H.relay, H.local_tiles = Layout(), Relay(), [{"url": "", "title": "clock"}]
        H.mcp = mcp_tiles.McpServer(mcp_tiles.TileRegistry(path), "test")
        srv = edged.Server(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]

        def req(method, url, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(method, url, body, {"Content-Type": "application/json"})
            r = c.getresponse()
            data = r.read()
            c.close()
            return r.status, data

        status, data = req("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                         "params": {"name": "register_tile", "arguments": TILE}}))
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["result"]["isError"])
        status, _ = req("POST", "/mcp", '{"jsonrpc":"2.0","method":"notifications/initialized"}')
        self.assertEqual(status, 202)
        self.assertEqual(req("GET", "/mcp")[0], 405)
        tiles = json.loads(req("GET", "/tiles")[1])["tiles"]
        self.assertIn(dict(TILE, online=True), tiles)


if __name__ == "__main__":
    unittest.main()
