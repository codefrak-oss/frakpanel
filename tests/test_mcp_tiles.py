"""Tests for the MCP tile server: python3 -m unittest discover -s tests"""
import base64
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
        self.assertEqual(names, {"frakpanel_guide", "register_tile", "list_tiles", "remove_tile",
                                 "get_layout", "set_slot", "screenshot"})
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

    def test_screenshot(self):
        png = mcp_tiles.PNG_SIGNATURE + b"fake image"
        server = mcp_tiles.McpServer(mcp_tiles.TileRegistry(self.path), "test", capture=lambda: png)
        res = self.rpc("tools/call", {"name": "screenshot", "arguments": {}}, server)["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(len(res["content"]), 1)
        item = res["content"][0]
        self.assertEqual((item["type"], item["mimeType"]), ("image", "image/png"))
        self.assertTrue(base64.b64decode(item["data"]).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_screenshot_fails(self):
        def broken():
            raise OSError("no display")
        server = mcp_tiles.McpServer(mcp_tiles.TileRegistry(self.path), "test", capture=broken)
        res, text = self.call("screenshot", server)
        self.assertTrue(res["isError"])
        self.assertIn("no display", text)
        res, text = self.call("screenshot")  # no capturer configured
        self.assertTrue(res["isError"])
        self.assertIn("no screen capture", text)

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


class McpLayoutTest(McpTest):
    """get_layout / set_slot on edged's real Layout, persisted to a temp layout file."""

    def make(self):
        import edged
        old = edged.LAYOUT_PATH
        edged.LAYOUT_PATH = self.layout_path = os.path.join(self.dir.name, "layout.json")
        self.addCleanup(setattr, edged, "LAYOUT_PATH", old)
        return mcp_tiles.McpServer(mcp_tiles.TileRegistry(self.path), "test", layout=edged.Layout())

    def test_get_layout(self):
        self.assertEqual(self.call("get_layout")[1], {"slots": ["", "", ""]})

    def test_set_slot(self):
        res, out = self.call("set_slot", slot=1, url=TILE["url"])
        self.assertFalse(res["isError"])
        self.assertEqual(out, {"slots": ["", TILE["url"], ""]})
        self.assertEqual(self.call("get_layout")[1], out)
        with open(self.layout_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), out)
        self.assertEqual(self.call("set_slot", slot=1, url="")[1], {"slots": ["", "", ""]})

    def test_set_slot_rejected(self):
        self.call("set_slot", slot=0, url=TILE["url"])
        for args in ({"slot": 3, "url": ""}, {"slot": -1, "url": ""}, {"slot": "1", "url": ""},
                     {"slot": True, "url": ""}, {"url": ""}, {"slot": 1, "url": "ftp://x/"}):
            res, msg = self.call("set_slot", **args)
            self.assertTrue(res["isError"], args)
        self.assertIn("slot must be an integer 0-2", self.call("set_slot", slot=3, url="")[1])
        self.assertEqual(self.call("get_layout")[1], {"slots": [TILE["url"], "", ""]})

    def test_modern_set_slot(self):
        body = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": "set_slot", "arguments": {"slot": 2, "url": TILE["url"]},
                           "_meta": {mcp_tiles.META_VERSION: MODERN}}}
        status, out = self.server.respond(json.dumps(body).encode(), {
            "MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call", "Mcp-Name": "set_slot"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(out["result"]["content"][0]["text"]), {"slots": ["", "", TILE["url"]]})


MODERN = "2026-07-28"


class Mcp20260728Test(unittest.TestCase):
    """The stateless 2026-07-28 protocol: per-request _meta, mirrored headers, server/discover."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server = mcp_tiles.McpServer(mcp_tiles.TileRegistry(os.path.join(self.dir.name, "t.json")), "test")

    def tearDown(self):
        self.dir.cleanup()

    def post(self, method, params=None, version=MODERN, headers=None, name=None):
        params = dict(params or {}, _meta={mcp_tiles.META_VERSION: version,
                                          "io.modelcontextprotocol/clientCapabilities": {}})
        if headers is None:
            headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
            if method == "tools/call":
                headers["Mcp-Name"] = name if name is not None else params["name"]
        return self.server.respond(json.dumps({"jsonrpc": "2.0", "id": 7, "method": method,
                                               "params": params}).encode(), headers)

    def test_versions(self):
        self.assertEqual(mcp_tiles.PROTOCOL_VERSIONS[0], MODERN)
        self.assertIn("2025-06-18", mcp_tiles.PROTOCOL_VERSIONS)
        self.assertIn("2025-03-26", mcp_tiles.PROTOCOL_VERSIONS)

    def test_discover(self):
        status, out = self.post("server/discover")
        self.assertEqual(status, 200)
        res = out["result"]
        self.assertEqual(res["supportedVersions"][0], MODERN)
        self.assertEqual(res["resultType"], "complete")
        self.assertIn("tools", res["capabilities"])
        self.assertEqual(res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "frakpanel")

    def test_tools_list_and_call(self):
        status, out = self.post("tools/list")
        self.assertEqual(status, 200)
        res = out["result"]
        self.assertEqual(res["resultType"], "complete")
        self.assertEqual(res["cacheScope"], "public")
        self.assertIsInstance(res["ttlMs"], int)
        self.assertEqual([t["name"] for t in res["tools"]],
                         ["frakpanel_guide", "register_tile", "list_tiles", "remove_tile", "get_layout", "set_slot", "screenshot"])
        status, out = self.post("tools/call", {"name": "register_tile", "arguments": TILE})
        self.assertEqual(status, 200)
        self.assertEqual(out["result"]["resultType"], "complete")
        self.assertFalse(out["result"]["isError"])
        self.assertIn("io.modelcontextprotocol/serverInfo", out["result"]["_meta"])
        self.assertEqual(self.server.registry.list(), [TILE])

    def test_mcp_name_base64(self):
        status, out = self.post("tools/call", {"name": "list_tiles", "arguments": {}},
                                name="=?base64?bGlzdF90aWxlcw==?=")
        self.assertEqual(status, 200, out)

    def test_unsupported_version(self):
        for version in ("1900-01-01", "2025-06-18"):  # 2025-06-18 is initialize-era, not per-request _meta
            status, out = self.post("tools/list", version=version)
            self.assertEqual(status, 400)
            self.assertEqual(out["error"]["code"], -32022)
            self.assertEqual(out["error"]["data"], {"supported": list(mcp_tiles.PROTOCOL_VERSIONS),
                                                    "requested": version})

    def test_unknown_header_version(self):
        status, out = self.server.respond(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                                          {"MCP-Protocol-Version": "2099-01-01"})
        self.assertEqual((status, out["error"]["code"]), (400, -32022))

    def test_header_mismatch(self):
        cases = [
            {"Mcp-Method": "tools/list"},  # MCP-Protocol-Version missing
            {"MCP-Protocol-Version": MODERN},  # Mcp-Method missing
            {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call"},  # wrong method
        ]
        for headers in cases:
            status, out = self.post("tools/list", headers=headers)
            self.assertEqual((status, out["error"]["code"]), (400, -32020), headers)
        status, out = self.post("tools/call", {"name": "list_tiles", "arguments": {}}, name="remove_tile")
        self.assertEqual((status, out["error"]["code"]), (400, -32020))
        status, out = self.post("tools/call", {"name": "list_tiles", "arguments": {}},
                                headers={"mcp-protocol-version": MODERN, "mcp-method": "tools/call"})
        self.assertEqual((status, out["error"]["code"]), (400, -32020))  # Mcp-Name missing
        status, out = self.server.respond(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                                          {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"})
        self.assertEqual((status, out["error"]["code"]), (400, -32020))  # header says modern, body has no _meta

    def test_removed_methods_are_404(self):
        for method in ("initialize", "ping", "nope"):
            status, out = self.post(method)
            self.assertEqual((status, out["error"]["code"]), (404, -32601), method)

    def test_legacy_initialize(self):
        def init(asked):
            status, out = self.server.respond(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                          "params": {"protocolVersion": asked}}).encode(), {})
            self.assertEqual(status, 200)
            return out["result"]["protocolVersion"]
        self.assertEqual(init("2025-06-18"), "2025-06-18")
        self.assertEqual(init("2025-03-26"), "2025-03-26")
        # 2026-07-28 has no initialize, so a handshake can only settle on a legacy version
        self.assertEqual(init(MODERN), "2025-06-18")
        self.assertEqual(init("1900-01-01"), "2025-06-18")
        status, out = self.server.respond(b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
                                          {"MCP-Protocol-Version": "2025-06-18"})
        self.assertEqual(status, 200)
        self.assertNotIn("resultType", out["result"])


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

        def req(method, url, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(method, url, body, dict(headers or {}, **{"Content-Type": "application/json"}))
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
        modern = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"}
        body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                           "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}})
        status, data = req("POST", "/mcp", body, modern)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["result"]["resultType"], "complete")
        status, data = req("POST", "/mcp", body, dict(modern, **{"Mcp-Method": "tools/call"}))
        self.assertEqual((status, json.loads(data)["error"]["code"]), (400, -32020))
        tiles = json.loads(req("GET", "/tiles")[1])["tiles"]
        self.assertIn(dict(TILE, online=True), tiles)


if __name__ == "__main__":
    unittest.main()
