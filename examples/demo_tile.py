"""
demo_tile.py -- a complete frakpanel tile served from a laptop, for proving the relay.

Serves one slot-sized page on 127.0.0.1:8790 (never the LAN; the panel host
reaches it through edgerelay.py). The page shows laptop-local state (hostname,
and battery on a Mac),
a server-sent-events stream (proves streaming responses survive the relay), and
a tap button whose round trip -- kiosk browser -> relay -> this laptop -> back -- is
timed, so relay latency is measured rather than guessed.

    python3 examples/demo_tile.py &
    python3 edgerelay.py --host <panel-host> --name air-demo --target 127.0.0.1:8790 --title "Demo"
    curl -XPOST http://<panel-host>:7781/slot -d '{"slot":0,"url":"relay://air-demo/"}'
"""
from __future__ import annotations

import http.server
import json
import socket
import subprocess
import threading
import time

PORT = 8790
HOST = socket.gethostname().split(".")[0]
taps = 0
taps_lock = threading.Lock()

PAGE = """<!doctype html><meta charset="utf-8"><title>edge demo</title>
<style>
html,body{margin:0;height:100%;background:#0b0d12;color:#c9ced8;font-family:system-ui,sans-serif;overflow:hidden;cursor:none;user-select:none;-webkit-user-select:none}
html.cursor,html.cursor *{cursor:auto}  /* ?cursor=1: the Mac preview widget, where hiding the pointer is a nuisance */
body{display:grid;grid-template-rows:auto 1fr auto;padding:3vh 3vw;box-sizing:border-box;gap:2vh}
h1{margin:0;font-size:min(6vh,4.5vw);font-weight:500;color:#fff}
.sub{font-size:min(3.4vh,2.6vw);color:#8a8f98}
button{all:unset;display:flex;flex-direction:column;align-items:center;justify-content:center;border-radius:2vh;background:#1c2a4a;color:#fff;font-size:min(10vh,7vw);font-weight:300;touch-action:manipulation}
button.hit{background:#2f4f8f}
.row{display:flex;justify-content:space-between;font-size:min(3.4vh,2.6vw);font-variant-numeric:tabular-nums}
.ok{color:#6fcf97}.bad{color:#eb5757}
</style>
<div><h1 id="host"></h1><div class="sub" id="batt">&nbsp;</div></div>
<button id="b"><span id="n">tap</span><span class="sub" id="rt">&nbsp;</span></button>
<div class="row"><span id="stream" class="bad">stream: connecting</span><span id="tick"></span></div>
<script>
if(new URLSearchParams(location.search).has('cursor'))document.documentElement.classList.add('cursor');
const $=id=>document.getElementById(id);
const times=[];
async function state(){
  try{const j=await (await fetch('/state',{cache:'no-store'})).json();$('host').textContent=j.host+' via relay';$('batt').textContent=j.battery;$('n').textContent=j.taps+' taps';}catch(e){}
}
$('b').addEventListener('pointerdown',async()=>{
  $('b').classList.add('hit');setTimeout(()=>$('b').classList.remove('hit'),120);
  const t0=performance.now();
  try{
    const j=await (await fetch('/tap',{method:'POST',cache:'no-store'})).json();
    const ms=performance.now()-t0;times.push(ms);if(times.length>20)times.shift();
    const med=[...times].sort((a,b)=>a-b)[Math.floor(times.length/2)];
    $('n').textContent=j.taps+' taps';
    $('rt').textContent='round trip '+ms.toFixed(0)+' ms · median '+med.toFixed(0)+' ms (n='+times.length+')';
  }catch(e){$('rt').textContent='tap failed';}
});
const es=new EventSource('/events');
es.onopen=()=>{$('stream').textContent='stream: live';$('stream').className='ok';};
es.onerror=()=>{$('stream').textContent='stream: reconnecting';$('stream').className='bad';};
es.onmessage=e=>{const j=JSON.parse(e.data);$('tick').textContent='laptop clock '+j.time+' · event #'+j.n;};
state();setInterval(state,5000);
</script>"""


def battery() -> str:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=2).stdout
        line = out.splitlines()[1] if len(out.splitlines()) > 1 else ""
        return line.split("\t", 1)[-1].split(" present")[0].strip() or "battery unknown"
    except (OSError, subprocess.SubprocessError):
        return "battery unknown"


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
            self.send(200, json.dumps({"host": HOST, "battery": battery(), "taps": taps}), "application/json")
        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            n = 0
            try:
                while True:
                    n += 1
                    self.wfile.write(f"data: {json.dumps({'time': time.strftime('%H:%M:%S'), 'n': n})}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                self.close_connection = True
        else:
            self.send(404, "not found", "text/plain")

    def do_POST(self) -> None:
        global taps
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/tap":
            with taps_lock:
                taps += 1
                n = taps
            self.send(200, json.dumps({"taps": n}), "application/json")
        else:
            self.send(404, "not found", "text/plain")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as srv:
        print(f"edge demo on http://127.0.0.1:{PORT}/", flush=True)
        srv.serve_forever()
