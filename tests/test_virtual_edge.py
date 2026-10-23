"""virtual_edge.py helpers and edged's GET /virtual: python3 -m unittest discover -s tests"""
import argparse
import http.client
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAKPANEL_DATA", tempfile.mkdtemp())
import edged  # noqa: E402
import virtual_edge as ve  # noqa: E402


class FitScaleTest(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(ve.fit_scale(2560, 720), 1.0)
        self.assertEqual(ve.fit_scale(1280, 360), 0.5)

    def test_wide_window_is_height_bound(self):
        self.assertEqual(ve.fit_scale(5000, 360), 0.5)

    def test_tall_window_is_width_bound(self):
        self.assertEqual(ve.fit_scale(1280, 1000), 0.5)


class ParseSizeTest(unittest.TestCase):
    def test_accepts(self):
        self.assertEqual(ve.parse_size("1280x360"), (1280, 360))
        self.assertEqual(ve.parse_size("1920X540"), (1920, 540))

    def test_rejects(self):
        for bad in ("", "1280", "x360", "1280x", "ax3", "0x360", "-1x2", "12.5x3"):
            with self.assertRaises(argparse.ArgumentTypeError, msg=bad):
                ve.parse_size(bad)

    def test_parse_args(self):
        a = ve.parse_args(["--size", "800x225", "--port", "9000", "--no-edged"])
        self.assertEqual((a.size, a.port, a.no_edged), ((800, 225), 9000, True))
        self.assertEqual(ve.parse_args([]).size, (1280, 360))


class ChromeArgsTest(unittest.TestCase):
    def test_args(self):
        args = ve.chrome_args("/c", "http://127.0.0.1:7781/virtual", (1280, 360), "/tmp/p")
        self.assertEqual(args[0], "/c")
        self.assertIn("--app=http://127.0.0.1:7781/virtual", args)
        self.assertIn("--window-size=1280,360", args)
        self.assertIn("--user-data-dir=/tmp/p", args)


class VirtualPageTest(unittest.TestCase):
    def test_served(self):
        srv = edged.Server(("127.0.0.1", 0), edged.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", "/virtual")
        r = c.getresponse()
        body = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertIn('src="/"', body)
        self.assertIn("width:2560px;height:720px", body)
        self.assertIn("overflow:hidden", body)
        self.assertIn("Math.min(W / 2560, H / 720)", body)
        self.assertIn("addEventListener('resize', fit)", body)
