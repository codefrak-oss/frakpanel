"""MCP server for registering URL tiles in the panel's picker, served by edged.

Transport: MCP Streamable HTTP at POST /mcp on edged's port (7781), stateless,
answered with application/json; no sessions, no SSE stream (GET /mcp is 405).
No auth, like the rest of edged: anything on the LAN can register a tile.

Protocol versions (PROTOCOL_VERSIONS), dual-era per the 2026-07-28 spec:
- 2026-07-28 (modern, preferred): no initialize; every request names its
  version in params._meta["io.modelcontextprotocol/protocolVersion"] and in the
  MCP-Protocol-Version header, plus Mcp-Method (and Mcp-Name for tools/call)
  headers that must match the body (else 400, HeaderMismatch -32020). One
  message per POST. server/discover lists the versions; an unsupported version
  is 400 UnsupportedProtocolVersion -32022, an unknown method 404 -32601.
  Results carry resultType "complete" and serverInfo in _meta; tools/list
  carries ttlMs and cacheScope.
- 2025-06-18 and 2025-03-26 (legacy): initialize negotiates one of them (a
  client asking for anything else gets 2025-06-18), then plain JSON-RPC, a
  batch allowed; a POST with no MCP-Protocol-Version header is legacy.

Tools: frakpanel_guide, register_tile {url, title}, list_tiles, remove_tile {url},
get_layout, set_slot {slot, url}, screenshot. get_layout/set_slot read and
change which tile each of the panel's slots 0-2 shows, through edged's Layout
(passed in, so validation and persistence are exactly POST /slot's).
screenshot returns the panel host's screen as a PNG image item, through a
capture callable passed in (edged gives it kiosk_platform.screenshot).
frakpanel_guide serves TILES.md and examples/self_hosted_tile.py from the
installed version's folder (the installers and release zips ship the tree).
Registrations live in mcp_tiles.json in $FRAKPANEL_DATA, apart from the
hand-edited local_tiles.json; GET /tiles reads them live, so no restart.
"""
import base64
import json
import os
import threading

PROTOCOL_VERSIONS = ("2026-07-28", "2025-06-18", "2025-03-26")
MODERN_VERSIONS = ("2026-07-28",)  # stateless, per-request _meta, no initialize
LEGACY_VERSIONS = tuple(v for v in PROTOCOL_VERSIONS if v not in MODERN_VERSIONS)  # initialize handshake
META_VERSION = "io.modelcontextprotocol/protocolVersion"
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022
TOOLS_TTL_MS = 3600000  # the tool list only changes with an edged upgrade
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
HERE = os.path.dirname(os.path.abspath(__file__))

GUIDE_INTRO = """# frakpanel: orientation for an MCP client

frakpanel is a wall/desk panel: a kiosk browser on a panel host (Windows or
Linux) showing a grid of tiles, run by edged (HTTP on port 7781). A tile is a
web page shown in an iframe on the panel; users pick tiles from the tile picker.

This MCP server (POST http://<panel-host>:7781/mcp, no auth, LAN only) has:
- frakpanel_guide: this text.
- register_tile {url, title}: add a URL tile to the picker (or retitle it).
  The url must be reachable from the panel host and allow being framed
  (no X-Frame-Options: DENY/SAMEORIGIN, no CSP frame-ancestors excluding it).
- list_tiles: every tile the panel's picker offers, in picker order: the
  clock (url ""), local_tiles.json entries, tiles registered here, relay
  tiles; each with url, title, online and deletable.
- remove_tile {url}: remove a tile registered here (the deletable ones that
  are not relay tiles).
- get_layout: the URL shown in each panel slot 0-2 ("" is the clock).
- set_slot {slot, url}: change what the panel shows in slot 0, 1 or 2.
- screenshot: a PNG image of what the panel host's screen shows right now.

## Fetching tiles and placing them in the three slots

The panel has three slots, 0 to 2, left to right; the url "" always means
the built-in clock, not a registered tile.

list_tiles returns every tile the panel's picker offers, the same list as
GET http://<panel-host>:7781/tiles: the clock (url ""), local_tiles.json
entries, the tiles registered through this MCP server, then relay tiles from
connected laptops, each with url, title, online and deletable.

Call get_layout first to see what each slot currently shows, then change one
slot at a time with set_slot {slot, url}. url is one of: "" for the clock,
relay://<name>/<path> for a relay tile, or an http(s):// URL the panel host
can reach.

Worked example: the user says "show the AI accounts tile in the middle
slot". The client fetches the tile list (list_tiles or GET /tiles), finds
the tile whose title matches "AI accounts", calls get_layout to see the
current slots, then calls set_slot {"slot": 1, "url": <that tile's url>}.

There is no auth and the last write wins, so before replacing a slot that
is not showing the clock, a client should confirm with the user first.

To add a tile backed by your own web server: either frame an existing page
(Route A/M below), or run a small tile server that fetches the upstream API
server-side and serves a frameable page (Route B; the runnable example
examples/self_hosted_tile.py is inlined at the end), then call register_tile
with its URL. The tile developer guide (TILES.md) follows.

"""


def _read(rel: str) -> str:
    with open(os.path.join(HERE, rel), encoding="utf-8") as f:
        return f.read()


def guide() -> str:
    """The orientation text; OSError if TILES.md cannot be read."""
    text = GUIDE_INTRO + _read("TILES.md")
    try:
        example = _read(os.path.join("examples", "self_hosted_tile.py"))
    except OSError as exc:
        return text + f"\n\n(examples/self_hosted_tile.py could not be read: {exc})\n"
    return (text + "\n\n---\n\n# examples/self_hosted_tile.py (runnable example, save and run with python3)\n\n"
            "```python\n" + example + "```\n")


class TileRegistry:
    """URL tiles registered through MCP, kept in one JSON file."""

    def __init__(self, path: str, log=lambda msg: None):
        self.path = path
        self.log = log
        self.lock = threading.Lock()
        self.tiles = self.load()

    @staticmethod
    def check(url, title) -> tuple[str, str]:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in ("http://", "https://"):
            raise ValueError(f"url must start with http:// or https://, got {url!r}")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must not be empty")
        return url, title.strip()

    def load(self) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                tiles = []
                for t in json.load(f):
                    url, title = self.check(t["url"], t["title"])
                    tiles.append({"url": url, "title": title})
                return tiles
        except FileNotFoundError:
            return []
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.log(f"ignoring {self.path}: {exc}")
            return []

    def save(self) -> None:  # caller holds self.lock
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.tiles, f, indent=2)
        os.replace(tmp, self.path)

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(t) for t in self.tiles]

    def register(self, url, title) -> dict:
        """Adds a tile, or retitles the one with this url."""
        url, title = self.check(url, title)
        with self.lock:
            for t in self.tiles:
                if t["url"] == url:
                    t["title"] = title
                    break
            else:
                self.tiles.append({"url": url, "title": title})
            self.save()
        return {"url": url, "title": title}

    def remove(self, url) -> bool:
        with self.lock:
            kept = [t for t in self.tiles if t["url"] != url]
            if len(kept) == len(self.tiles):
                return False
            self.tiles = kept
            self.save()
        return True


TOOLS = [
    {
        "name": "frakpanel_guide",
        "description": "Call this first: explains what frakpanel is, what a tile is, how to build a tile "
                       "(framing a page, or a small self-hosted tile server; X-Frame-Options/CSP pitfalls) "
                       "and how to register it with register_tile. Returns the tile developer guide and "
                       "the runnable example examples/self_hosted_tile.py.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "register_tile",
        "description": "Add a URL tile to the frakpanel tile picker (or retitle it if the url is already registered). "
                       "The page must be reachable from the panel host and allow being framed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http:// or https:// URL of the tile page"},
                "title": {"type": "string", "description": "Name shown in the picker"},
            },
            "required": ["url", "title"],
        },
    },
    {
        "name": "list_tiles",
        "description": "List every tile the panel's picker offers, in picker order: the clock (url \"\"), local_tiles.json entries, tiles registered through this MCP server, relay tiles; each with url, title, online and deletable.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "remove_tile",
        "description": "Remove a URL tile registered through this MCP server, by its url.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The registered tile's url"}},
            "required": ["url"],
        },
    },
    {
        "name": "get_layout",
        "description": "Which tile URL the panel shows in each of its slots 0-2, as {\"slots\": [url, url, url]} "
                       "(\"\" is the clock).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_slot",
        "description": "Change what the panel shows: put a tile in slot 0, 1 or 2. Returns the new layout.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slot": {"type": "integer", "minimum": 0, "maximum": 2, "description": "The slot, 0-2"},
                "url": {"type": "string", "description": "\"\" for the clock, relay://<name>/<path>, "
                                                         "or an http(s) URL (e.g. a tile from list_tiles)"},
            },
            "required": ["slot", "url"],
        },
    },
    {
        "name": "screenshot",
        "description": "A PNG screenshot of what the panel host's screen (the Edge panel included) shows right now, "
                       "as an image content item.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


INSTRUCTIONS = ("Register URL tiles for the frakpanel tile picker, and choose which tile the panel shows in "
                "each slot 0-2 (get_layout, set_slot: set_slot changes what the panel shows), and "
                "see the screen with screenshot. Call "
                "frakpanel_guide first to learn what frakpanel is and how to build tiles. No auth; LAN only.")


def _text(obj, error: bool = False) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return {"content": [{"type": "text", "text": text}], "isError": error}


class McpServer:
    def __init__(self, registry: TileRegistry, version: str, log=lambda msg: None, layout=None, capture=None,
                 tiles=None):
        self.registry = registry
        self.tiles = tiles  # () -> the picker's tile list (GET /tiles); None lists only the registry's tiles
        self.capture = capture  # () -> PNG bytes, raising OSError on failure; None leaves screenshot erroring
        self.layout = layout  # edged.Layout: get() and set_slot({slot, url}); None leaves the layout tools erroring
        self.version = version
        self.log = log

    def call_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "frakpanel_guide":
                try:
                    return _text(guide())
                except OSError as exc:
                    return _text(f"the guide could not be read: {exc}", error=True)
            if name == "register_tile":
                tile = self.registry.register(args.get("url"), args.get("title"))
                self.log(f"mcp register_tile {tile}")
                return _text({"registered": tile})
            if name == "list_tiles":
                return _text({"tiles": self.tiles() if self.tiles else self.registry.list()})
            if name == "remove_tile":
                url = args.get("url")
                removed = self.registry.remove(url)
                if removed:
                    self.log(f"mcp remove_tile {url!r}")
                    return _text({"removed": url})
                return _text(f"no registered tile with url {url!r}", error=True)
            if name in ("get_layout", "set_slot"):
                if self.layout is None:
                    return _text("no panel layout is available", error=True)
                if name == "get_layout":
                    return _text({"slots": self.layout.get()})
                slots = self.layout.set_slot({"slot": args.get("slot"), "url": args.get("url")})
                self.log(f"mcp set_slot {args.get('slot')} {args.get('url')!r}")
                return _text({"slots": slots})
            if name == "screenshot":
                return self.screenshot()
        except ValueError as exc:
            return _text(str(exc), error=True)
        raise KeyError(name)

    def screenshot(self) -> dict:
        if self.capture is None:
            return _text("no screen capture is available on this panel host", error=True)
        try:
            png = self.capture()
        except Exception as exc:  # a capture failure is the caller's error, never the server's
            return _text(f"the screen could not be captured: {exc}", error=True)
        if not isinstance(png, bytes) or not png.startswith(PNG_SIGNATURE):
            return _text("the screen capture did not produce a PNG", error=True)
        return {"content": [{"type": "image", "data": base64.b64encode(png).decode("ascii"),
                             "mimeType": "image/png"}], "isError": False}

    def handle_one(self, msg) -> dict | None:
        """One JSON-RPC message in; its response out, or None for a notification."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
                return None  # a response from the client; we never ask anything
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        if "id" not in msg:
            return None  # notifications/initialized and friends
        mid, method = msg["id"], msg["method"]
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return _error(mid, -32602, "params must be an object")
        if method == "initialize":  # legacy only: 2026-07-28 has no handshake
            asked = params.get("protocolVersion")
            return _result(mid, {
                "protocolVersion": asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": self.server_info(),
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": TOOLS})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if not isinstance(args, dict):
                return _error(mid, -32602, "arguments must be an object")
            try:
                return _result(mid, self.call_tool(name, args))
            except KeyError:
                return _error(mid, -32602, f"unknown tool {name!r}")
        return _error(mid, -32601, f"method not found: {method}")

    def server_info(self) -> dict:
        return {"name": "frakpanel", "version": self.version}

    def handle_modern(self, msg: dict, headers) -> tuple[int, dict | None]:
        """One 2026-07-28 message (its _meta names a modern version) in; (HTTP status, JSON or None)."""
        mid, method, params = msg.get("id"), msg["method"], msg.get("params")
        version = params["_meta"][META_VERSION]
        if version not in MODERN_VERSIONS:
            return 400, _error(mid, UNSUPPORTED_VERSION, "Unsupported protocol version",
                               {"supported": list(PROTOCOL_VERSIONS), "requested": version})
        if "id" not in msg:
            return 202, None  # 2026-07-28 defines no client notifications over HTTP; accept and ignore
        expect = {"MCP-Protocol-Version": version, "Mcp-Method": method}
        if method == "tools/call":
            expect["Mcp-Name"] = params.get("name")
        for name, want in expect.items():
            got = _header(headers, name)
            if got is None:
                return 400, _error(mid, HEADER_MISMATCH, f"Header mismatch: {name} header missing")
            if name == "Mcp-Name":
                got = _decode_header(got)
            if got != want:
                return 400, _error(mid, HEADER_MISMATCH,
                                   f"Header mismatch: {name} header value {got!r} does not match body value {want!r}")
        meta = {"io.modelcontextprotocol/serverInfo": self.server_info()}
        if method == "server/discover":
            return 200, _result(mid, {"resultType": "complete", "supportedVersions": list(PROTOCOL_VERSIONS),
                                      "capabilities": {"tools": {}}, "instructions": INSTRUCTIONS,
                                      "ttlMs": TOOLS_TTL_MS, "cacheScope": "public", "_meta": meta})
        if method == "tools/list":
            return 200, _result(mid, {"resultType": "complete", "tools": TOOLS,
                                      "ttlMs": TOOLS_TTL_MS, "cacheScope": "public", "_meta": meta})
        if method == "tools/call":
            out = self.handle_one(msg)
            if "result" in out:
                out["result"] = dict(out["result"], resultType="complete", _meta=meta)
            return 200, out
        return 404, _error(mid, -32601, f"method not found: {method}")

    def respond(self, raw: bytes, headers=None) -> tuple[int, dict | list | None]:
        """A POST body and its headers in; (HTTP status, the JSON to answer with or None for no body)."""
        headers = headers or {}
        try:
            msg = json.loads(raw)
        except ValueError:
            return 200, _error(None, -32700, "parse error")
        asked = _header(headers, "MCP-Protocol-Version")
        if asked is not None and asked not in PROTOCOL_VERSIONS:
            mid = msg.get("id") if isinstance(msg, dict) else None
            return 400, _error(mid, UNSUPPORTED_VERSION, "Unsupported protocol version",
                               {"supported": list(PROTOCOL_VERSIONS), "requested": asked})
        modern = _modern(msg)
        if modern or asked in MODERN_VERSIONS:
            if not modern:
                mid = msg.get("id") if isinstance(msg, dict) else None
                return 400, _error(mid, HEADER_MISMATCH, f"Header mismatch: MCP-Protocol-Version {asked} needs "
                                   f"params._meta[{META_VERSION!r}] and a single JSON-RPC request")
            return self.handle_modern(msg, headers)
        out = self.handle(raw)
        return (202, None) if out is None else (200, out)

    def handle(self, raw: bytes) -> dict | list | None:
        """A legacy (initialize-era) POST body in; the JSON to answer with, or None (answer 202)."""
        try:
            msg = json.loads(raw)
        except ValueError:
            return _error(None, -32700, "parse error")
        if isinstance(msg, list):
            out = [r for r in (self.handle_one(m) for m in msg) if r is not None]
            return out or None
        return self.handle_one(msg)


def _result(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": error}


def _modern(msg) -> bool:
    """A single JSON-RPC request whose params._meta names a protocol version (2026-07-28 style)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return False
    params = msg.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return isinstance(meta, dict) and isinstance(meta.get(META_VERSION), str)


def _header(headers, name: str) -> str | None:
    """A request header by case-insensitive name; headers is a dict or an http.client.HTTPMessage."""
    if hasattr(headers, "get_all"):
        return headers.get(name)
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _decode_header(value: str) -> str | None:
    """Undoes the spec's =?base64?...?= sentinel encoding of a header value."""
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[len("=?base64?"):-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return value
