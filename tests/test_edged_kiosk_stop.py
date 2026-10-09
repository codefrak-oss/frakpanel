"""POST /kiosk/stop and /kiosk/start on edged's Handler: python3 -m unittest discover -s tests"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAKPANEL_DATA", tempfile.mkdtemp())
import edged  # noqa: E402


class Kiosk:
    windows = None  # no window layer: pin()/release() have nothing to raise

    def __init__(self):
        self.kills = 0

    def kill(self):
        self.kills += 1


class Layout:
    def get(self):
        return ["", "", ""]


class Relay:
    def status(self):
        return {}


class KioskStopTest(unittest.TestCase):
    def setUp(self):
        self.kiosk = Kiosk()
        saved = edged._kiosk, edged.kiosk_stopped, edged.restart_at, edged.pinned, edged.repin_at
        edged._kiosk = self.kiosk
        self.addCleanup(self.restore, saved)

        class H(edged.Handler):
            pass
        H.layout, H.relay = Layout(), Relay()
        srv = edged.Server(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.port = srv.server_address[1]

    @staticmethod
    def restore(saved):
        edged._kiosk, edged.kiosk_stopped, edged.restart_at, edged.pinned, edged.repin_at = saved

    def req(self, method, url, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, url, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def layout(self):
        return self.req("GET", "/layout")[1]

    def test_bad_seconds(self):
        for bad in (0, -5, 86401, "60", True, [1]):
            self.assertEqual(self.req("POST", "/kiosk/stop", {"seconds": bad})[0], 400, bad)
        self.assertEqual(self.kiosk.kills, 0)
        self.assertEqual(self.layout()["kiosk"], "running")

    def test_running_by_default(self):
        j = self.layout()
        self.assertEqual((j["kiosk"], j["restart_in"]), ("running", None))

    def test_timed_stop_then_start(self):
        self.assertEqual(self.req("POST", "/kiosk/stop", {"seconds": 900}),
                         (200, {"stopped": True, "restart_in": 900}))
        self.assertEqual(self.kiosk.kills, 1)
        j = self.layout()
        self.assertEqual(j["kiosk"], "stopped")
        self.assertIn(j["restart_in"], (899, 900))
        edged.pinned = False
        self.assertEqual(self.req("POST", "/kiosk/start"), (200, {"started": True}))
        j = self.layout()
        self.assertEqual((j["kiosk"], j["restart_in"], j["pinned"]), ("running", None, True))

    def test_untimed_stop(self):
        self.assertEqual(self.req("POST", "/kiosk/stop")[1], {"stopped": True, "restart_in": None})
        j = self.layout()
        self.assertEqual((j["kiosk"], j["restart_in"]), ("stopped", None))
        self.assertFalse(edged.may_run_kiosk(time.monotonic() + 10 ** 6))

    def test_supervisor_gate(self):
        self.assertTrue(edged.may_run_kiosk())
        self.req("POST", "/kiosk/stop", {"seconds": 60})
        now = time.monotonic()
        self.assertFalse(edged.may_run_kiosk(now))
        self.assertFalse(edged.may_run_kiosk(now + 30))
        self.assertTrue(edged.may_run_kiosk(now + 61))  # timer ended: the stop is cleared
        self.assertFalse(edged.kiosk_stopped)
        self.assertTrue(edged.may_run_kiosk(now))
        self.assertEqual(self.layout()["kiosk"], "running")


if __name__ == "__main__":
    unittest.main()
