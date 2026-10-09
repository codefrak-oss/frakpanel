# A tile allowed to rearrange the panel's slots
Status: proposed
Decision log:
Ticket: #23

## The question

Should a registered tile be able to change what the panel shows in slots 0-2, and if so, through which plumbing? The motivating case: a tile placed in the middle slot (1) has its own "left" and "right" pages; on a tap it puts them into slots 0 and 2, within about a second. The registrant decides at registration whether the tile may do this; a tile without that permission cannot change the layout. It must work when the tile's web server cannot open connections to edged, and the tile must be able to list the tiles the panel knows (`GET /tiles`, MCP `list_tiles`) to choose what goes where.

## Today, without any change

- `edged.py:358` (`Handler.reply`) sends `Access-Control-Allow-Origin: *` on every response, and `edged.py:401-405` (`do_OPTIONS`) answers the CORS preflight allowing `GET, POST` and `Content-Type`. `edged.py:438-442` sends `POST /slot` straight to `Layout.set_slot` (`edged.py:310-318`) with no check of who asked.
- Each slot is an `<iframe>` in the kiosk shell (`edged.py:180`), so a tile's page runs in the kiosk browser on the host. A relay slot resolves to `http://127.0.0.1:<port>/…` (`edged.py:365-369`, `edgerelay.py` header), so its page can `fetch('http://127.0.0.1:7781/slot', {method:'POST', …})` and it succeeds today. A page can also `GET /tiles` the same way.
- So any tile, permitted or not, can already rearrange the panel from its page. The permission flag is meaningless unless the browser path is gated; each option below says what it does about it.
- The shell polls `GET /layout` every 2 s (`edged.py:250`) and rebuilds slots when it changes (`edged.py:195`), so anything that lands in `Layout.set_slot` reaches the screen within 2 s, plus the tile's own round trip.

Note on "the tile web server can't connect to the daemon": for a laptop tile this is the designed state. Only the relay's outbound control and stream connections cross to the host (TILES.md, "Connectivity"); the host never dials the laptop. Port 7781 is reachable from the laptop only "for curl" and is not needed.

## Proposal

Three options are laid out, plus building nothing. The recommendation (end of the doc) is **B**.

### A. Tile server contract, long-polled by edged (the user's sketch)

The tile's web server implements two endpoints, e.g. `GET /.frakpanel/layout-requests?wait=25` (held open until the tile has a request, answering `{"slots":{"0":"…","2":"…"}}` or 204 on timeout) and `POST /.frakpanel/tiles` (edged pushes the tile list to it when it changes). edged runs one long-poll loop per permitted tile.

- **Grant at registration.** Relay: a new `edgerelay.py --may-arrange` flag, sent in `hello` as `"arrange": true`. `local_tiles.json`: an optional `"arrange": true` per entry. MCP: `register_tile {url, title, arrange}`.
- **Where stored.** Relay: `edge_relays.json` today holds only name→port; it gains a name→`{port, arrange}` map (or a sibling `edge_relay_perms.json`), since titles are not persisted today. `local_tiles.json`: the hand-edited file itself. MCP: the entry in `mcp_tiles.json`.
- **Listing tiles.** edged pushes the `/tiles` list to the contract endpoint (or the page fetches `GET /tiles` from the browser, which works today).
- **Latency.** The long poll returns at once on the tap; edged sets the slot; the shell sees it on its next 2 s poll. 0-2 s plus the hop; under 1 s if the shell is also tightened (see "Tightening").
- **Server cannot reach edged.** Fine for relay tiles: edged dials the long poll through the relay's existing per-browser stream mechanism (`open` op, the laptop dials a stream back). MCP and `local_tiles.json` tiles are URLs the host frames directly; many have no server edged should poll (a static page, a third-party site), and polling arbitrary registered URLs from edged is new outbound traffic from the host. For them A works only if their server implements the contract.
- **Browser path.** A does not close it. Unless B's Origin gate is also added, the flag stays bypassable.
- **Files.** `edged.py` (poller, flag in `/tiles`, MCP passthrough), `edgerelay.py` (`--may-arrange`, `hello` field, persistence), `mcp_tiles.py` (`register_tile` arg, stored field), `TILES.md`, `README.md`, `examples/demo_tile.py` (sample contract), `tests/test_edged_picker.py`, `tests/test_mcp_tiles.py`, a new `tests/test_arrange_poll.py`.

### B. The tile's page calls `POST /slot` from the kiosk browser, gated by the permission

Keep the mechanism that already works and add the missing check. In `do_POST` for `/slot` and `/layout`, edged reads the request's `Origin` header. No `Origin` (curl, MCP, scripts) and the shell's own origin (the picker) pass as today. Any other origin is a tile page: edged maps it to a registered tile (a relay's stable port identifies its name; an MCP or local tile by its URL's origin) and allows the call only if that tile is registered with `arrange` and currently sits in a slot. Otherwise 403. The tile lists tiles with `GET /tiles` from the same page.

- **Grant at registration.** Same three switches as A: `edgerelay.py --may-arrange` (`hello` gains `"arrange": true`), `"arrange": true` in a `local_tiles.json` entry, `register_tile {url, title, arrange}`.
- **Where stored.** As in A: relay permission persisted next to the port in `edge_relays.json` (so a permitted relay keeps it while away and across edged restarts), `local_tiles.json` itself, `mcp_tiles.json` entries. A reconnecting relay's `hello` re-sends and updates it.
- **Listing tiles.** `GET /tiles` from the page (CORS already allows it), which also reports `arrange` per tile.
- **Latency.** The tap's fetch reaches edged in milliseconds (same host); the screen changes on the shell's next poll, 0-2 s. Under 1 s with "Tightening".
- **Server cannot reach edged.** Irrelevant: the call is made by the browser on the host, never by the tile's server. A laptop tile's server needs no route to 7781.
- **Browser path.** Kept as the mechanism, and closed to tiles without the permission.
- **Limit.** `Origin` is set by the browser and cannot be forged by a page, but curl can send any header. That is the same trust as today: the gate stops a misbehaving tile, not someone on the LAN (README "Security").
- **Files.** `edged.py` (Origin gate, `arrange` in `/tiles`, relay origin lookup), `edgerelay.py` (`--may-arrange`, `hello`, persistence), `mcp_tiles.py` (`register_tile` arg, stored field), `TILES.md`, `README.md`, `examples/demo_tile.py` (a left/right demo), `tests/test_edged_picker.py`, `tests/test_mcp_tiles.py`.

### C. A layout op on the relay control connection

The laptop's `edgerelay.py` client, already connected outbound, accepts layout requests from the tile server on a local endpoint (e.g. `POST 127.0.0.1:<relay-local-port>/arrange`) and forwards them as `{"op":"layout","slot":0,"url":"…"}` on the control connection; edged answers with `{"op":"layout-ok","slots":[…]}` or an error, and can push `{"op":"tiles","tiles":[…]}` when the list changes.

- **Grant at registration.** Relay: `--may-arrange`, sent in `hello`. `local_tiles.json` and MCP tiles have no relay, so C cannot serve them; they get no way to arrange (or fall back to B).
- **Where stored.** `edge_relays.json` as above; edged's `run_control` (`edgerelay.py:245-270`) rejects `layout` from a name without it.
- **Listing tiles.** The `tiles` op pushed on the control connection; the relay client exposes it locally.
- **Latency.** Control connection is open, so milliseconds to edged; 0-2 s to the screen (under 1 s with "Tightening").
- **Server cannot reach edged.** Works: the only connection is the one the relay already dials out.
- **Browser path.** C does not close it by itself; needs B's gate too, or the flag is bypassable.
- **Files.** `edgerelay.py` (new ops on both sides, local endpoint), `edged.py` (wire Relay to Layout, tiles push), `TILES.md`, `README.md`, `examples/demo_tile.py`, a new `tests/test_relay_layout.py`. `mcp_tiles.py` unchanged.

### D. Build nothing yet

Leave it as it is: any tile page can already post to `/slot`, with no permission. Files: none.

### Tightening the 2 s

Any option lands in `Layout.set_slot`, so the shell picks it up within 2 s. To get "about a second": either drop the shell's poll to 500 ms (cheap, local), or have `GET /layout?since=<key>` long-poll (edged holds it until the layout changes). For B there is a third, simplest path: the tile's iframe posts `parent.postMessage('frakpanel:layout')` and the shell polls at once. Recommended with B: shell long-polls `/layout` (removes the latency for every option and every caller, including MCP `set_slot`).

## Alternatives, and why not

| | permission enforceable | all 3 registration routes | works when tile server can't reach edged | new protocol | latency |
|---|---|---|---|---|---|
| A | only with B's gate added | relay yes; URL tiles only if they implement the contract | yes (via relay streams) | two endpoints + poller | ≤2 s (<1 s tightened) |
| **B** | yes, for browser calls | yes | yes (the browser calls, not the server) | none, one header check | ≤2 s (<1 s tightened) |
| C | only with B's gate added | relay only | yes | two control ops + local endpoint | ≤2 s (<1 s tightened) |
| D | no | n/a | n/a | none | n/a |

- **A, why not:** it builds a poller for something the tile's page can already do in one fetch, cannot reach MCP and `local_tiles.json` tiles that have no cooperating server, and still leaves the browser path open, so it needs B anyway.
- **C, why not:** relay tiles only, adds ops to an unauthenticated protocol, and also needs B to make the flag mean anything. Worth it only if tiles must arrange without a tap in their page (e.g. a laptop-side event), which the ask does not name.
- **D, why not:** the user wants the feature, and today's unguarded `/slot` is the opposite of "a tile without permission cannot change the layout".

## How it sits with "no auth anywhere" and "the host never knows what a button means"

- TILES.md "No auth anywhere. edged trusts the LAN" and README "Security": the permission is not authentication. It is a registrant's switch that stops a tile's own page from moving others, enforced by the browser's `Origin`; anything on the LAN with curl can still set any slot. The wording stays true; TILES.md gains one line: "A tile registered with `arrange` may set slots from its page; others get 403. This is a guard, not auth." README "Security" unchanged.
- README "the host never knows what a button means": B keeps it. edged learns only "this tile asked for URL X in slot N", the same message the picker sends; what the tap meant stays in the tile. No wording change. (A and C keep it too.)

## Open questions

- Is the permission granted by the registrant only, or also revocable from the panel's picker? Assumed registration only, as the ask says; a picker toggle could come later.
- Must the requesting tile be in a slot to arrange? Assumed yes, so an offscreen page cannot act.
- May a permitted tile use `POST /layout` (all three at once), or only `/slot`? Assumed both; the middle-tile case needs two slots, which two `/slot` calls or one `/layout` do.

## Implementation outline

1. `mcp_tiles.py`: `register_tile` takes optional `arrange` (bool, default false), stores it in `mcp_tiles.json`; `list_tiles` reports it.
2. `edged.py`: `load_local_tiles` reads optional `arrange`; `GET /tiles` reports `arrange` for every tile.
3. `edgerelay.py`: client flag `--may-arrange` sends `"arrange": true` in `hello`; server records it per name and persists it in `edge_relays.json` with the port (old file format still read).
4. `edged.py`: in `do_POST` for `/slot` and `/layout`, an Origin gate: no Origin or the shell's origin passes; otherwise map the origin to a tile (relay port→name, URL origin→MCP/local tile) and require `arrange` and presence in a slot, else 403 `{"error":"tile may not arrange"}`.
5. `edged.py`: shell long-polls `GET /layout?since=<key>` (held up to 25 s) instead of the 2 s interval, keeping a fallback poll.
6. `examples/demo_tile.py`: a left/right demo behind `--may-arrange`.
7. `TILES.md`: a section "Rearranging the panel" (flag, `fetch` example, 403, the one guard line); README: registration flag in the relay and local tiles docs.
8. Tests: gate (allowed, denied, no Origin, picker), persistence of the relay flag, MCP `arrange` round trip, `GET /tiles` field.

## Cost

One feature ticket, tier 5, about 5 points: one or two Smith runs (implementation plus a review round). A or C would each add a run.

## Recommendation

**B.** The tile's page already runs in the kiosk browser on the host and can already call `POST /slot`, so the only thing missing is the permission, and an `Origin` check enforces it for all three registration routes with no new protocol; it works whether or not the tile's server can reach edged, because the browser makes the call. A and C each need B's gate anyway to make the flag mean anything, so they add plumbing without removing it; build B together with the shell's long poll on `/layout` for the sub-second feel.
