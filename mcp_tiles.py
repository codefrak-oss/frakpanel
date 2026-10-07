"""MCP server for registering URL tiles in the panel's picker, served by edged.

Transport: MCP Streamable HTTP at POST /mcp on edged's port (7781), stateless:
each POST is one JSON-RPC 2.0 message (or a batch) answered with
application/json; no sessions, no SSE stream (GET /mcp is 405). No auth, like
the rest of edged: anything on the LAN can register a tile.

Tools: frakpanel_guide, register_tile {url, title}, list_tiles, remove_tile {url}.
frakpanel_guide serves TILES.md and examples/self_hosted_tile.py from the
installed version's folder (the installers and release zips ship the tree).
Registrations live in mcp_tiles.json in $FRAKPANEL_DATA, apart from the
hand-edited local_tiles.json; GET /tiles reads them live, so no restart.
"""
import json
import os
import threading

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
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
- list_tiles / remove_tile {url}: see and remove tiles registered here.

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
        "description": "List the URL tiles registered through this MCP server.",
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
]


def _text(obj, error: bool = False) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return {"content": [{"type": "text", "text": text}], "isError": error}


class McpServer:
    def __init__(self, registry: TileRegistry, version: str, log=lambda msg: None):
        self.registry = registry
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
                return _text({"tiles": self.registry.list()})
            if name == "remove_tile":
                url = args.get("url")
                removed = self.registry.remove(url)
                if removed:
                    self.log(f"mcp remove_tile {url!r}")
                    return _text({"removed": url})
                return _text(f"no registered tile with url {url!r}", error=True)
        except ValueError as exc:
            return _text(str(exc), error=True)
        raise KeyError(name)

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
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(mid, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "frakpanel", "version": self.version},
                "instructions": "Register URL tiles for the frakpanel tile picker. Call frakpanel_guide first to learn "
                                "what frakpanel is and how to build tiles. No auth; LAN only.",
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

    def handle(self, raw: bytes) -> dict | list | None:
        """A POST body in; the JSON to answer with, or None (answer 202)."""
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


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}
