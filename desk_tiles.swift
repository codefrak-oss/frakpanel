// desk_tiles.swift -- frakpanel tiles pinned to the Mac desktop, scaled and clickable.
//
// One borderless window per tile at desktop level (like Übersicht's widgets),
// stacked in the top-right corner of the main screen, on every Space; window
// managers that tile only normal windows (AeroSpace) leave them alone. Each
// tile is a top-level WKWebView scaled with pageZoom, so it lays out at the
// Edge slot size (812x720) and takes clicks; an iframe scaled with CSS zoom or
// transform in Übersicht's WebKit swallows every click.
//
// A menu bar item (▦) sets the size, how many tiles, which tile goes in each
// slot (from the hub's picker, GET /tiles) and the hub. Relay tiles load from
// their port on the hub (GET /relays) and host-local ones from the hub's name,
// as remote virtual Edge windows do; ?cursor=1 keeps the pointer visible.
// Settings persist in UserDefaults (net.codefrak.frakpanel.desk-tiles).
//
//     swiftc -O desk_tiles.swift -o desk_tiles
//     ./desk_tiles --hub http://nuc.local:7781    # the hub is remembered after

import AppKit
import WebKit

let W: CGFloat = 812, H: CGFloat = 720  // one Edge slot
let SIZES: [(String, CGFloat)] = [("xs", 0.3), ("s", 0.4), ("m", 0.5), ("l", 0.6), ("xl", 0.8)]
let MAX_TILES = 4
let GAP: CGFloat = 10, MARGIN: CGFloat = 24
let defaults = UserDefaults(suiteName: "net.codefrak.frakpanel.desk-tiles")!

struct Tile { let url: String; let title: String; let online: Bool }

final class TileWebView: WKWebView {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }  // a click acts at once, not just focuses
}

final class TilePanel: NSPanel {
    override var canBecomeKey: Bool { true }  // so tiles can take typing
}

final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var hub = defaults.string(forKey: "hub") ?? ""
    var size = defaults.string(forKey: "size") ?? "l"
    var count = max(1, min(MAX_TILES, defaults.integer(forKey: "count")))
    var slots = defaults.stringArray(forKey: "slots") ?? []
    var tiles: [Tile] = []
    var relays: [String: Int] = [:]
    var panels: [(NSPanel, TileWebView)] = []
    var loaded: [URL?] = []  // what each panel was last told to load
    let status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

    func applicationDidFinishLaunching(_ note: Notification) {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--hub"), i + 1 < args.count { hub = args[i + 1]; save() }
        status.button?.title = "▦"
        NotificationCenter.default.addObserver(self, selector: #selector(layout),
            name: NSApplication.didChangeScreenParametersNotification, object: nil)
        if hub.isEmpty { askHub() }
        refresh()
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { _ in self.refresh() }
    }

    func save() {
        defaults.set(hub, forKey: "hub"); defaults.set(size, forKey: "size")
        defaults.set(count, forKey: "count"); defaults.set(slots, forKey: "slots")
    }

    var scale: CGFloat { SIZES.first { $0.0 == size }?.1 ?? 0.6 }

    // -- the hub --

    func get(_ path: String, _ done: @escaping (Any?) -> Void) {
        guard let url = URL(string: hub + path) else { return done(nil) }
        var req = URLRequest(url: url); req.timeoutInterval = 5
        URLSession.shared.dataTask(with: req) { data, _, _ in
            let obj = data.flatMap { try? JSONSerialization.jsonObject(with: $0) }
            DispatchQueue.main.async { done(obj) }
        }.resume()
    }

    func refresh() {
        get("/tiles") { obj in
            guard let list = (obj as? [String: Any])?["tiles"] as? [[String: Any]] else { return self.rebuild() }
            self.tiles = list.map { Tile(url: $0["url"] as? String ?? "", title: $0["title"] as? String ?? "?",
                                         online: $0["online"] as? Bool ?? true) }
            self.get("/relays") { obj in
                self.relays = (obj as? [String: [String: Any]] ?? [:]).compactMapValues { $0["port"] as? Int }
                self.rebuild()
            }
        }
    }

    func pick(_ i: Int) -> String {
        if i < slots.count, tiles.contains(where: { $0.url == slots[i] }) { return slots[i] }
        return tiles.isEmpty ? (i < slots.count ? slots[i] : "") : tiles[min(i, tiles.count - 1)].url
    }

    // a picker URL as this Mac can load it
    func frameURL(_ raw: String) -> URL? {
        guard let hubURL = URL(string: hub), let host = hubURL.host else { return nil }
        var c: URLComponents?
        if raw.isEmpty {
            c = URLComponents(string: hub + "/home")
        } else if raw.hasPrefix("relay://") {
            let rest = raw.dropFirst("relay://".count)
            let name = String(rest.prefix { $0 != "/" })
            guard let port = relays[name] else {
                c = URLComponents(string: hub + "/home")
                c?.queryItems = [URLQueryItem(name: "waiting", value: name)]
                return add(cursor: c)
            }
            c = URLComponents(string: "http://\(host):\(port)/" + rest.dropFirst(name.count).drop { $0 == "/" })
        } else {
            c = URLComponents(string: raw)
            if c?.host == "127.0.0.1" || c?.host == "localhost" { c?.host = host }
        }
        return add(cursor: c)
    }

    func add(cursor c: URLComponents?) -> URL? {
        guard var c = c else { return nil }
        c.queryItems = (c.queryItems ?? []).filter { $0.name != "cursor" } + [URLQueryItem(name: "cursor", value: "1")]
        return c.url
    }

    // -- windows --

    func rebuild() {
        while panels.count > count { let (p, _) = panels.removeLast(); p.close(); loaded.removeLast() }
        while panels.count < count { panels.append(makePanel()); loaded.append(nil) }
        for (i, (_, web)) in panels.enumerated() {
            guard let url = frameURL(pick(i)) else { continue }
            if loaded[i] != url {  // the tile's own navigation is left alone
                loaded[i] = url
                web.load(URLRequest(url: url))
            }
        }
        layout()
        buildMenu()
    }

    func makePanel() -> (NSPanel, TileWebView) {
        let p = TilePanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 1)
        p.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenNone]
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.hidesOnDeactivate = false
        let web = TileWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.wantsLayer = true
        web.layer?.cornerRadius = 6
        web.layer?.masksToBounds = true
        web.underPageBackgroundColor = .black
        web.navigationDelegate = self
        p.contentView = web
        p.orderFront(nil)
        return (p, web)
    }

    @objc func layout() {
        guard let screen = NSScreen.main else { return }
        let s = scale, w = W * s, h = H * s
        var top = screen.visibleFrame.maxY - MARGIN
        for (p, web) in panels {
            p.setFrame(NSRect(x: screen.visibleFrame.maxX - MARGIN - w, y: top - h, width: w, height: h), display: true)
            web.pageZoom = s  // the page lays out at 812x720 CSS px and draws at s
            top -= h + GAP
        }
    }

    // WebKit drops the zoom when a page loads, so set it again on every load
    func webView(_ web: WKWebView, didCommit navigation: WKNavigation!) { web.pageZoom = scale }
    func webView(_ web: WKWebView, didFinish navigation: WKNavigation!) { web.pageZoom = scale }

    // -- menu --

    func buildMenu() {
        let m = NSMenu()
        let sizes = NSMenu()
        for (name, s) in SIZES {
            let it = item("\(name)  (\(Int(s * 100))%)", #selector(setSize(_:)), name)
            it.state = name == size ? .on : .off
            sizes.addItem(it)
        }
        m.addItem(sub("Size", sizes))
        let counts = NSMenu()
        for n in 1...MAX_TILES {
            let it = item("\(n)", #selector(setCount(_:)), n)
            it.state = n == count ? .on : .off
            counts.addItem(it)
        }
        m.addItem(sub("Tiles", counts))
        m.addItem(.separator())
        for i in 0..<count {
            let menu = NSMenu()
            for t in tiles {
                let it = item(t.title + (t.online ? "" : " (offline)"), #selector(setSlot(_:)), [i, t.url] as [Any])
                it.state = t.url == pick(i) ? .on : .off
                menu.addItem(it)
            }
            if tiles.isEmpty { menu.addItem(NSMenuItem(title: "hub not answering", action: nil, keyEquivalent: "")) }
            let title = tiles.first { $0.url == pick(i) }?.title ?? "?"
            m.addItem(sub("Slot \(i + 1): \(title)", menu))
        }
        m.addItem(.separator())
        m.addItem(item("Reload tiles", #selector(reloadAll), nil))
        m.addItem(item("Hub: \(hub.isEmpty ? "not set" : hub)…", #selector(changeHub), nil))
        m.addItem(.separator())
        m.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        status.menu = m
    }

    func item(_ title: String, _ action: Selector, _ obj: Any?) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: action, keyEquivalent: "")
        it.target = self; it.representedObject = obj
        return it
    }

    func sub(_ title: String, _ menu: NSMenu) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: nil, keyEquivalent: ""); it.submenu = menu
        return it
    }

    @objc func setSize(_ it: NSMenuItem) { size = it.representedObject as! String; save(); layout(); buildMenu() }

    @objc func setCount(_ it: NSMenuItem) {
        slots = (0..<max(count, slots.count)).map { pick($0) }
        count = it.representedObject as! Int; save(); rebuild()
    }

    @objc func setSlot(_ it: NSMenuItem) {
        let a = it.representedObject as! [Any]
        let i = a[0] as! Int
        slots = (0..<max(count, slots.count)).map { pick($0) }
        slots[i] = a[1] as! String
        save(); rebuild()
    }

    @objc func reloadAll() { panels.forEach { $0.1.reload() }; refresh() }

    @objc func changeHub() { askHub(); refresh() }

    func askHub() {
        let a = NSAlert()
        a.messageText = "frakpanel hub"
        a.informativeText = "The machine running edged, e.g. http://nuc.local:7781"
        let f = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        f.stringValue = hub.isEmpty ? "http://" : hub
        a.accessoryView = f
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn {
            hub = f.stringValue.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            save()
        }
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // no Dock icon; the menu bar item is the UI
app.run()
