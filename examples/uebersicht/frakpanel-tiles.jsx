// frakpanel-tiles.jsx — Übersicht widget: one or more frakpanel tiles stacked
// in the top-right corner. Hover a tile and click ⚙ to set the size, how many
// tiles, and which tile goes in each slot (from the hub's picker, GET /tiles).
// Settings live in localStorage. Relay tiles load from their port on the hub
// (GET /relays), as remote virtual Edge windows do; ?cursor=1 keeps the pointer.
//
// Scaling: transform: scale() drops every click in Übersicht's WebKit, so
// tiles use zoom. WebKit applies a zoomed iframe's zoom twice (its page lays
// out at width * zoom, then draws at zoom again), so the iframe is W/s x H/s:
// the page lays out at 812x720 and draws at s, inside a box clipped to it.
//
// Install (any Mac that can reach the hub on 7781 and the tile ports):
//   curl -fsSL https://raw.githubusercontent.com/codefrak-oss/frakpanel/main/examples/uebersicht/frakpanel-tiles.jsx \
//     | sed 's#http://HUB:7781#http://<your hub>:7781#' \
//     > ~/Library/Application\ Support/Übersicht/widgets/frakpanel-tiles.jsx

const HUB = "http://HUB:7781";  // your hub (the machine running edged), e.g. http://nuc.local:7781
const KEY = "frakpanel-tiles";
const W = 812, H = 720;  // one Edge slot
const SIZES = { xs: 0.3, s: 0.4, m: 0.5, l: 0.6, xl: 0.8 };
const MAX = 4;

export const command = `curl -s -m5 ${HUB}/tiles; echo; curl -s -m5 ${HUB}/relays`;
export const refreshFrequency = 60000;

const load = () => {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
};
const save = s => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) {} };

export const initialState = { output: "", setup: false, size: "l", count: 1, slots: [], ...load() };

export const updateState = (event, prev) => {
  if (event.type === "UB/COMMAND_RAN") return { ...prev, output: event.output || prev.output };
  if (event.type === "SET") {
    const next = { ...prev, ...event.patch };
    const { setup, size, count, slots } = next;
    save({ size, count, slots });
    return next;
  }
  return prev;
};

export const className = `
  top: 40px; right: 24px;
  font-family: -apple-system, "SF Pro Text", Helvetica, sans-serif;
  font-size: 11px; color: #9aa0a8;
  -webkit-font-smoothing: antialiased; -webkit-user-select: none; user-select: none;

  .tiles { display: flex; flex-direction: column; gap: 10px; align-items: flex-end; }
  .box { position: relative; overflow: hidden; background: #000; border-radius: 6px;
         box-shadow: 0 0 0 1px rgba(255,255,255,0.12), 0 8px 30px rgba(0,0,0,0.35); }
  .box iframe { border: 0; background: #000; display: block; }
  .gear { position: absolute; top: 6px; right: 6px; width: 22px; height: 22px; border-radius: 11px;
          background: rgba(20,22,28,0.85); color: #e6e8eb; font-size: 14px; line-height: 22px;
          text-align: center; cursor: pointer; opacity: 0; transition: opacity .15s; }
  .tiles:hover .gear, .gear.on { opacity: 1; }
  .setup { background: rgba(20,22,28,0.95); border: 1px solid #2c313b; border-radius: 8px;
           padding: 10px 12px; margin-bottom: 10px; color: #c9ced8; min-width: 280px; }
  .row { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
  .row > span { width: 44px; color: #9aa0a8; }
  .opt { padding: 2px 8px; border-radius: 4px; background: #1a1d24; border: 1px solid #2c313b;
         cursor: pointer; color: #c9ced8; }
  .opt.sel { background: #2b5cd6; border-color: #2b5cd6; color: #fff; }
  select { flex: 1; background: #1a1d24; color: #e6e8eb; border: 1px solid #2c313b;
           border-radius: 5px; font-size: 11px; padding: 2px 4px; }
  .msg { padding: 8px; color: #e0a0a0; }
`;

const hubHost = new URL(HUB).hostname;

// a picker URL as this Mac's browser can load it
function frameUrl(url, relays) {
  if (!url) return `${HUB}/home?cursor=1`;  // the clock
  let u;
  if (url.startsWith("relay://")) {
    const [name, ...rest] = url.slice("relay://".length).split("/");
    const r = relays[name];
    if (!r) return `${HUB}/home?waiting=${encodeURIComponent(name)}&cursor=1`;
    u = new URL(`http://${hubHost}:${r.port}/${rest.join("/")}`);
  } else {
    u = new URL(url);
    if (u.hostname === "127.0.0.1" || u.hostname === "localhost") u.hostname = hubHost;
  }
  u.searchParams.set("cursor", "1");
  return u.href;
}

export const render = ({ output, setup, size, count, slots }, dispatch) => {
  let tiles, relays;
  try {
    const [a, b] = output.trim().split("\n");
    tiles = JSON.parse(a).tiles;
    relays = JSON.parse(b);
  } catch (e) {
    return <div className="msg">frakpanel hub {HUB} not answering</div>;
  }
  const set = patch => dispatch({ type: "SET", patch });
  const s = SIZES[size] || SIZES.l;
  const known = new Set(tiles.map(t => t.url));
  const pick = i => (known.has(slots[i]) ? slots[i] : (tiles[i] || tiles[0] || {}).url);
  const setSlot = (i, url) => {
    const next = Array.from({ length: count }, (_, j) => pick(j));
    next[i] = url;
    set({ slots: next });
  };
  return (
    <div>
      {setup && (
        <div className="setup">
          <div className="row"><span>size</span>
            {Object.keys(SIZES).map(k => (
              <a key={k} className={"opt" + (k === size ? " sel" : "")} onClick={() => set({ size: k })}>{k}</a>
            ))}
          </div>
          <div className="row"><span>tiles</span>
            {Array.from({ length: MAX }, (_, j) => j + 1).map(n => (
              <a key={n} className={"opt" + (n === count ? " sel" : "")} onClick={() => set({ count: n })}>{n}</a>
            ))}
          </div>
          {Array.from({ length: count }, (_, i) => (
            <div className="row" key={i}><span>slot {i + 1}</span>
              <select value={pick(i)} onChange={e => setSlot(i, e.target.value)}>
                {tiles.map(t => (
                  <option key={t.url} value={t.url}>{t.title}{t.online ? "" : " (offline)"}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
      <div className="tiles">
        {Array.from({ length: count }, (_, i) => (
          <div className="box" key={i} style={{ width: W * s, height: H * s }}>
            <iframe src={frameUrl(pick(i), relays)} style={{ width: W / s, height: H / s, zoom: s }} />
            {i === 0 && <a className={"gear" + (setup ? " on" : "")} onClick={() => set({ setup: !setup })}>⚙</a>}
          </div>
        ))}
      </div>
    </div>
  );
};
