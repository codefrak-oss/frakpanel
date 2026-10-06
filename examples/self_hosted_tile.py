"""
self_hosted_tile.py -- a frakpanel tile that polls a self-hosted HTTP API, for the
common case of showing data from a LAN service (not laptop-local state like demo_tile.py).

Serves one slot-sized page on 127.0.0.1:<port> (never the LAN; the panel host
reaches it through edgerelay.py). A background thread polls the configured
upstream URL server-side -- the browser never talks to the upstream directly,
so there's no CORS or mixed-origin problem -- and the page shows the latest
response, or a readable error if the upstream is unreachable.

    export SELF_HOSTED_TILE_UPSTREAM=http://nas.example.lan:8080/api/status
    python3 examples/self_hosted_tile.py &
    python3 edgerelay.py --host <panel-host> --name air-nas --target 127.0.0.1:8791 --title "NAS"
    curl -XPOST http://<panel-host>:7781/slot -d '{"slot":0,"url":"relay://air-nas/"}'

Or pass the upstream on the command line instead of the environment:

    python3 examples/self_hosted_tile.py --upstream http://nas.example.lan:8080/api/status
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import threading
import time
import urllib.error
import urllib.request

PAGE = """<!doctype html><meta charset="utf-8"><title>self-hosted tile</title>
<style>
html,body{margin:0;height:100%;background:#0b0d12;color:#c9ced8;font-family:system-ui,sans-serif;overflow:hidden;cursor:none;user-select:none;-webkit-user-select:none}
html.cursor,html.cursor *{cursor:auto}
body{display:grid;grid-template-rows:auto 1fr auto;padding:3vh 3vw;box-sizing:border-box;gap:2vh}
h1{margin:0;font-size:min(5vh,4vw);font-weight:500;color:#fff}
.sub{font-size:min(3vh,2.4vw);color:#8a8f98}
pre{margin:0;overflow:auto;font-size:min(3vh,2.2vw);white-space:pre-wrap;word-break:break-word}
.ok{color:#6fcf97}.bad{color:#eb5757}
.row{display:flex;justify-content:space-between;font-size:min(2.6vh,2vw);font-variant-numeric:tabular-nums;color:#8a8f98}
</style>
<div><h1>Self-hosted tile</h1><div class="sub" id="src"></div></div>
<pre id="body">loading...</pre>
<div class="row"><span id="status"></span><span id="tick"></span></div>
<script>
if(new URLSearchParams(location.search).has('cursor'))document.documentElement.classList.add('cursor');
const $=id=>document.getElementById(id);
async function refresh(){
  try{
    const j=await (await fetch('/state',{cache:'no-store'})).json();
    $('src').textContent=j.upstream;
    if(j.ok){
      $('status').textContent='ok';$('status').className='ok';
      $('body').textContent=JSON.stringify(j.data,null,2);
    }else{
      $('status').textContent='unreachable';$('status').className='bad';
      $('body').textContent=j.error;
    }
    $('tick').textContent='checked '+new Date(j.checked_at*1000).toLocaleTimeString();
  }catch(e){
    $('status').textContent='tile error';$('status').className='bad';
  }
}
refresh();setInterval(refresh,3000);
</script>"""


class Upstream:
    """Polls `url` every `interval` seconds in a background thread and keeps the latest result."""

    def __init__(self, url: str, interval: float) -> None:
        self.url = url
        self.interval = interval
        self.lock = threading.Lock()
        self.state = {"upstream": url, "ok": False, "data": None, "error": "not polled yet", "checked_at": 0.0}

    def poll_once(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=5) as resp:
                body = resp.read()
            try:
                data = json.loads(body)
            except ValueError:
                data = body.decode("utf-8", "replace")
            with self.lock:
                self.state = {"upstream": self.url, "ok": True, "data": data, "error": None, "checked_at": time.time()}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            with self.lock:
                self.state = {
                    "upstream": self.url,
                    "ok": False,
                    "data": None,
                    "error": f"{type(e).__name__}: {e}",
                    "checked_at": time.time(),
                }

    def run(self) -> None:
        while True:
            self.poll_once()
            time.sleep(self.interval)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)


def make_handler(upstream: Upstream) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args) -> None:
            pass

        def send(self, code: int, body: str, ctype: str) -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]  # tiles get query strings, e.g. /?cursor=1
            if path == "/":
                self.send(200, PAGE, "text/html; charset=utf-8")
            elif path == "/state":
                self.send(200, json.dumps(upstream.snapshot()), "application/json")
            else:
                self.send(404, "not found", "text/plain")

    return Handler


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    parser = argparse.ArgumentParser(description="A frakpanel tile that polls a self-hosted HTTP API server-side.")
    parser.add_argument(
        "--upstream",
        default=os.environ.get("SELF_HOSTED_TILE_UPSTREAM"),
        help="URL to poll (default: $SELF_HOSTED_TILE_UPSTREAM)",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("SELF_HOSTED_TILE_PORT", "8791")))
    parser.add_argument("--interval", type=float, default=5.0, help="poll interval in seconds (default: 5)")
    args = parser.parse_args()

    if not args.upstream:
        parser.error("an upstream URL is required: --upstream or $SELF_HOSTED_TILE_UPSTREAM")

    upstream = Upstream(args.upstream, args.interval)
    threading.Thread(target=upstream.run, daemon=True).start()

    with Server(("127.0.0.1", args.port), make_handler(upstream)) as srv:
        print(f"self-hosted tile on http://127.0.0.1:{args.port}/ polling {args.upstream}", flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    main()
