// edge-tile-preview.jsx — Übersicht desktop widget that shows a Xeneon Edge
// tile at its real slot size (812x720 CSS px), so tile work can be checked
// on the Mac desktop without walking to the panel.
//
// It iframes the tile's local URL directly (no relay, no panel host), so what you see
// is the same page the panel's kiosk frames. That makes it a real check of layout
// and framing (a page that forbids framing is blank here too).
//
// Clicks reach the tile, but only because it's shrunk with CSS `zoom`. With
// `transform: scale()` on the iframe, WebKit dropped every click (2026-09-13:
// the demo's tap counter stayed at 0). Keep it `zoom`. Clicks are mouse
// events, not touch, and it renders in Übersicht's WebKit, not the panel's
// Chrome; see TILES.md, "See it", for touch emulation.
//
// Install:
//   ln -s /path/to/frakpanel/examples/uebersicht/edge-tile-preview.jsx \
//     ~/Library/Application\ Support/Übersicht/widgets/
// Point it at a tile by editing TILE_URL below (Übersicht reloads the widget
// on save). "reload" in the header reloads the tile after you change the page.

const TILE_URL = "http://127.0.0.1:8790/?cursor=1";  // examples/demo_tile.py by default.
// ?cursor=1 asks a tile to show the mouse pointer, which tiles hide for the
// panel (TILES.md, "The contract"). A tile that ignores it just hides the
// pointer over the frame; nothing else breaks.
const SCALE = 0.6;  // 1 = true CSS size; 0.6 (487x432pt) fits the right 40% of a 1280pt-wide desktop
const W = 812, H = 720;  // one Edge slot; fixed, see README "Fixed slots"

export const command = "true";
export const refreshFrequency = false;

export const className = `
  top: 40px; right: 24px;
  font-family: -apple-system, "SF Pro Text", Helvetica, sans-serif;
  font-size: 11px; color: #9aa0a8;
  -webkit-font-smoothing: antialiased; -webkit-user-select: none; user-select: none;

  .head { width: ${W * SCALE}px; display: flex; justify-content: space-between; align-items: center;
          padding: 0 2px 4px; box-sizing: border-box; }
  .head b { color: #e6e8eb; font-weight: 600; }
  .head a { color: #9aa0a8; cursor: pointer; text-decoration: none; margin-right: 12px; }  /* links on the left: desktop icons sit top-right and draw over widgets */
  .head a:hover { color: #e6e8eb; }
  .box { width: ${W}px; height: ${H}px; zoom: ${SCALE}; overflow: hidden; background: #000;
         border-radius: ${6 / SCALE}px; box-shadow: 0 0 0 ${1 / SCALE}px rgba(255,255,255,0.12), 0 8px 30px rgba(0,0,0,0.35); }
  iframe { width: ${W}px; height: ${H}px; border: 0; background: #000; }
`;

const reload = () => {
  const f = document.getElementById("edge-tile-preview-frame");
  if (f) f.src = TILE_URL;
};

export const render = () => (
  <div>
    <div className="head">
      <span>
        <a onClick={reload}>reload</a>
        <a href={TILE_URL}>open</a>
      </span>
      <span><b>Edge tile</b> · {W}x{H}{SCALE !== 1 ? ` @ ${SCALE}` : ""} · {TILE_URL}</span>
    </div>
    <div className="box">
      <iframe id="edge-tile-preview-frame" src={TILE_URL} />
    </div>
  </div>
);
