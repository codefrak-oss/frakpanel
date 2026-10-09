r"""
edgerelay.py -- lets a laptop serve a frakpanel tile without accepting inbound connections.

The laptop dials *out* to edged on the panel host, so laptop firewalls and
VPNs that block inbound ports don't matter. The host's kiosk browser still
loads plain http URLs; edged just carries the bytes.

    kiosk browser --> 127.0.0.1:<client port> (edged) ==relay stream==> laptop --> 127.0.0.1:<target> (laptop web server)

Protocol: TCP port 7782 (EDGE_RELAY_PORT), each connection starts with one JSON line.
  control connection (one per relay name, stays open):
    laptop -> host  {"op":"hello","name":"air-demo","title":"Demo"}
                    title is what the panel's tile picker shows for this relay
                    (optional; defaults to the name)
    host -> laptop  {"op":"welcome","port":7790}
    host -> laptop  {"op":"open","id":"<hex>"}     a browser connected; dial a stream for it
    laptop -> host  {"op":"ping"}  /  host -> laptop {"op":"pong"}   (dropped after TIMEOUT silent)
  stream connection (one per browser connection, dialed by the laptop on "open"):
    laptop -> host  {"op":"stream","id":"<hex>"}
    then raw bytes both ways, spliced to the browser's socket. No HTTP parsing,
    so keep-alive, streaming responses and WebSocket upgrades pass through as-is,
    and a slow response on one connection never stalls another.

Each client name gets its own 127.0.0.1-only listener port on the panel host
(stable across reconnects, recorded in edge_relays.json), so pages can use
absolute paths without any rewriting. A slot refers to it as
relay://<name>/<path>; edged resolves that to http://127.0.0.1:<port>/<path>
while the client is connected, and to its /home fallback while it isn't.

The server also remembers every name that has connected since it started, with
its title, so the picker can list a laptop's tile (greyed) while it is away.

No auth: anything that can reach 7782 and says hello with a name gets that
name's tile. LAN only.

Server side: RelayServer, run inside edged.py.
Client side:  python3 edgerelay.py --host <panel-host> --name air-demo --target 127.0.0.1:8790 --title "Demo"
This file is standard library only and is all a laptop needs.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import socketserver
import sys
import threading
import time

RELAY_PORT = int(os.environ.get("EDGE_RELAY_PORT", "7782"))
FIRST_CLIENT_PORT = 7790
TIMEOUT = 15.0  # seconds without a control message before a client is dropped
PING_EVERY = 5.0
STREAM_WAIT = 5.0  # seconds a browser connection waits for the laptop to dial its stream
HERE = os.path.dirname(os.path.abspath(__file__))


def read_line(sock: socket.socket, limit: int = 4096) -> bytes:
    """Read one \\n-terminated line byte by byte, so nothing after it is consumed from the socket."""
    buf = bytearray()
    while len(buf) < limit:
        ch = sock.recv(1)
        if not ch:
            break
        if ch == b"\n":
            return bytes(buf)
        buf += ch
    raise ConnectionError("no line")


def splice(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes both ways until both directions close. Blocks until done."""

    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    for s in (a, b):
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    t = threading.Thread(target=pump, args=(b, a), daemon=True)
    t.start()
    pump(a, b)
    t.join()
    for s in (a, b):
        try:
            s.close()
        except OSError:
            pass


# ---------------------------------------------------------------- server (panel host)

class Client:
    def __init__(self, name: str, sock: socket.socket, port: int, title: str):
        self.name = name
        self.sock = sock
        self.port = port
        self.title = title
        self.last_seen = time.monotonic()
        self.wlock = threading.Lock()

    def send(self, obj: dict) -> None:
        with self.wlock:
            self.sock.sendall((json.dumps(obj) + "\n").encode())


class RelayServer:
    def __init__(self, log, state_path: str = os.path.join(os.environ.get("FRAKPANEL_DATA", HERE), "edge_relays.json")):
        self.log = log
        self.lock = threading.Lock()
        self.clients: dict[str, Client] = {}
        self.pending: dict[str, tuple[threading.Event, list]] = {}  # stream id -> (ready, [laptop socket])
        self.listeners: dict[str, socket.socket] = {}
        self.titles: dict[str, str] = {}  # every name seen since start -> its last title (for the picker)
        self.state_path = state_path
        try:
            with open(state_path, encoding="utf-8") as f:
                self.ports: dict[str, int] = {str(k): int(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            self.ports = {}

    # -- lookups used by edged --
    def url_for(self, name: str, path: str) -> str | None:
        with self.lock:
            client = self.clients.get(name)
            return f"http://127.0.0.1:{client.port}/{path.lstrip('/')}" if client else None

    def status(self) -> dict:
        with self.lock:
            return {n: {"port": c.port, "title": c.title, "idle": round(time.monotonic() - c.last_seen, 1)} for n, c in self.clients.items()}

    def tiles(self) -> list[dict]:
        """One picker entry per name seen since start: connected ones first, then the rest greyed."""
        with self.lock:
            return sorted(
                ({"url": f"relay://{n}/", "title": t, "laptop": n, "online": n in self.clients} for n, t in self.titles.items()),
                key=lambda e: (not e["online"], e["title"].lower()),
            )

    def forget(self, name: str) -> bool | None:
        """Drops an away name from the picker (it comes back when that laptop reconnects).
        None: never seen; False: connected now, so not forgotten."""
        with self.lock:
            if name not in self.titles:
                return None
            if name in self.clients:
                return False
            del self.titles[name]
            return True

    # -- ports --
    def port_for(self, name: str) -> int:
        with self.lock:
            if name not in self.ports:
                self.ports[name] = max(self.ports.values(), default=FIRST_CLIENT_PORT - 1) + 1
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.ports, f, indent=2)
                os.replace(tmp, self.state_path)
            return self.ports[name]

    def ensure_listener(self, name: str, port: int) -> None:
        with self.lock:
            if name in self.listeners:
                return
            ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ls.bind(("127.0.0.1", port))
            ls.listen(64)
            self.listeners[name] = ls
        threading.Thread(target=self.accept_browsers, args=(name, ls), daemon=True).start()

    def accept_browsers(self, name: str, ls: socket.socket) -> None:
        while True:
            conn, _ = ls.accept()
            threading.Thread(target=self.serve_browser, args=(name, conn), daemon=True).start()

    def serve_browser(self, name: str, conn: socket.socket) -> None:
        with self.lock:
            client = self.clients.get(name)
        if client is None:
            body = f"{name} is not connected".encode()
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nConnection: close\r\n"
                         + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            conn.close()
            return
        sid = secrets.token_hex(8)
        ready, slot = threading.Event(), []
        with self.lock:
            self.pending[sid] = (ready, slot)
        try:
            client.send({"op": "open", "id": sid})
        except OSError:
            pass
        ok = ready.wait(STREAM_WAIT)
        with self.lock:
            self.pending.pop(sid, None)
        if not ok or not slot:
            self.log(f"relay {name}: stream {sid} never arrived")
            conn.close()
            return
        splice(conn, slot[0])

    # -- relay port --
    def serve(self) -> None:
        server = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                sock: socket.socket = self.request
                try:
                    msg = json.loads(read_line(sock))
                except (ValueError, OSError):
                    return
                if msg.get("op") == "stream":
                    server.attach_stream(str(msg.get("id")), sock)
                elif msg.get("op") == "hello":
                    name = str(msg.get("name") or self.client_address[0])
                    server.run_control(name, sock, self.client_address[0], str(msg.get("title") or name)[:40])

        class TCP(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        threading.Thread(target=self.reap, daemon=True).start()
        with TCP(("0.0.0.0", RELAY_PORT), Handler) as srv:
            self.log(f"relay listening on 0.0.0.0:{RELAY_PORT}")
            srv.serve_forever()

    def attach_stream(self, sid: str, sock: socket.socket) -> None:
        with self.lock:
            entry = self.pending.get(sid)
        if entry is None:
            sock.close()
            return
        entry[1].append(sock)
        entry[0].set()
        # the browser thread now owns sock and splices it; keep this handler thread
        # from returning (socketserver would close the socket) until the splice ends
        while sock.fileno() != -1:
            time.sleep(1)

    def run_control(self, name: str, sock: socket.socket, addr: str, title: str) -> None:
        port = self.port_for(name)
        try:
            self.ensure_listener(name, port)
        except OSError as exc:
            self.log(f"relay {name}: cannot listen on 127.0.0.1:{port}: {exc}")
            return
        client = Client(name, sock, port, title)
        with self.lock:
            old = self.clients.get(name)
            self.clients[name] = client  # newest connection for a name wins
            self.titles[name] = title
        if old:
            try:
                old.sock.close()
            except OSError:
                pass
        self.log(f"relay + {name} ({title}) from {addr} -> 127.0.0.1:{port}")
        try:
            client.send({"op": "welcome", "port": port})
            while True:
                line = read_line(sock)
                client.last_seen = time.monotonic()
                if json.loads(line).get("op") == "ping":
                    client.send({"op": "pong"})
        except (OSError, ValueError, ConnectionError):
            pass
        finally:
            with self.lock:
                if self.clients.get(name) is client:
                    del self.clients[name]
            self.log(f"relay - {name}")

    def reap(self) -> None:
        while True:
            time.sleep(TIMEOUT / 3)
            now = time.monotonic()
            with self.lock:
                stale = [c for c in self.clients.values() if now - c.last_seen > TIMEOUT]
            for c in stale:
                self.log(f"relay timeout {c.name}")
                try:
                    c.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


# ---------------------------------------------------------------- client (laptop)

def run_client(host: str, name: str, target: tuple[str, int], title: str | None = None) -> None:
    backoff = 1.0
    while True:
        try:
            ctl = socket.create_connection((host, RELAY_PORT), timeout=5)
            ctl.settimeout(None)
            ctl.sendall((json.dumps({"op": "hello", "name": name, "title": title or name}) + "\n").encode())
            welcome = json.loads(read_line(ctl))
            print(f"{time.strftime('%H:%M:%S')} connected to {host}:{RELAY_PORT} as {name}; panel-host port {welcome.get('port')}", flush=True)
            backoff = 1.0
            wlock = threading.Lock()
            alive = threading.Event()
            alive.set()

            def pinger() -> None:
                while alive.is_set():
                    time.sleep(PING_EVERY)
                    try:
                        with wlock:
                            ctl.sendall(b'{"op":"ping"}\n')
                    except OSError:
                        return

            threading.Thread(target=pinger, daemon=True).start()
            try:
                while True:
                    msg = json.loads(read_line(ctl))
                    if msg.get("op") == "open":
                        threading.Thread(target=open_stream, args=(host, str(msg["id"]), target), daemon=True).start()
            finally:
                alive.clear()
                ctl.close()
        except (OSError, ValueError, ConnectionError) as exc:
            print(f"{time.strftime('%H:%M:%S')} relay: {exc!r}; retrying in {backoff:.0f}s", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


def open_stream(host: str, sid: str, target: tuple[str, int]) -> None:
    try:
        local = socket.create_connection(target, timeout=5)
        remote = socket.create_connection((host, RELAY_PORT), timeout=5)
        local.settimeout(None)
        remote.settimeout(None)
        remote.sendall((json.dumps({"op": "stream", "id": sid}) + "\n").encode())
    except OSError as exc:
        print(f"{time.strftime('%H:%M:%S')} stream {sid}: {exc!r}", flush=True)
        return
    splice(remote, local)


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve a local web server to a frakpanel panel through an outbound relay.")
    ap.add_argument("--host", default=os.environ.get("FRAKPANEL_HOST"),
                    help="the panel host running edged.py (default: $FRAKPANEL_HOST)")
    ap.add_argument("--name", default=socket.gethostname().split(".")[0].lower())
    ap.add_argument("--target", default="127.0.0.1:8790", help="local host:port to expose")
    ap.add_argument("--title", help="what the panel's tile picker calls this tile (default: the name)")
    args = ap.parse_args()
    if not args.host:
        ap.error("--host (or $FRAKPANEL_HOST) is required")
    target_host, _, port = args.target.rpartition(":")
    run_client(args.host, args.name, (target_host or "127.0.0.1", int(port)), args.title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
