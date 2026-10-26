"""updater.update_now (the virtual Edge app's update at launch) against a fake GitHub:
python3 -m unittest discover -s tests"""
import hashlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater  # noqa: E402


def release_zip(ver: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in (("VERSION", ver), ("edged.py", "# edged"), ("launcher.py", f"# launcher {ver}"),
                           ("virtual_edge.py", "# ve"), ("updater.py", "# up")):
            z.writestr(f"frakpanel-{ver}/{name}", body)
    return buf.getvalue()


class FakeGitHub:
    def __init__(self, releases: list[tuple[str, bool]]):
        self.zips = {ver: release_zip(ver) for ver, _ in releases}
        self.downloads = []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/repos/"):
                    body = json.dumps(fake.listing).encode()
                elif self.path.startswith("/asset/"):
                    ver = self.path.rsplit("/", 1)[1]
                    fake.downloads.append(ver)
                    body = fake.zips[ver]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{self.srv.server_port}"
        self.listing = [{"tag_name": f"v{ver}", "prerelease": pre, "assets": [
            {"name": f"frakpanel-{ver}.zip", "url": f"{self.api}/asset/{ver}",
             "digest": "sha256:" + hashlib.sha256(self.zips[ver]).hexdigest()}]} for ver, pre in releases]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class UpdateNowTest(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub([("0.1.0", False), ("0.2.0-beta.7", True)])
        self.addCleanup(self.gh.close)
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "data"))
        self.platform = sys.platform
        sys.platform = "darwin"  # the source zip, not the Windows one
        self.addCleanup(setattr, sys, "platform", self.platform)

    def config(self, **cfg):
        with open(os.path.join(self.root, "data", "update.json"), "w") as f:
            json.dump(dict(api=self.gh.api, **cfg), f)

    def state(self):
        with open(os.path.join(self.root, "launcher.json")) as f:
            return json.load(f)

    def test_fresh_install_on_beta_takes_the_newest_beta(self):
        self.config(channel="beta")
        self.assertEqual(updater.update_now(self.root, log=lambda m: None), "0.2.0-beta.7")
        self.assertEqual(self.state(), {"current": "0.2.0-beta.7", "previous": None, "pending": False})
        self.assertTrue(os.path.isfile(os.path.join(self.root, "versions", "0.2.0-beta.7", "virtual_edge.py")))
        with open(os.path.join(self.root, "launcher.py")) as f:
            self.assertEqual(f.read(), "# launcher 0.2.0-beta.7")

    def test_stable_skips_betas_and_channel_argument_overrides(self):
        self.config(channel="beta")
        self.assertEqual(updater.update_now(self.root, "stable", log=lambda m: None), "0.1.0")

    def test_up_to_date_downloads_nothing(self):
        self.config(channel="stable")
        updater.update_now(self.root, log=lambda m: None)
        self.assertEqual(updater.update_now(self.root, log=lambda m: None), "0.1.0")
        self.assertEqual(self.gh.downloads, ["0.1.0"])

    def test_update_keeps_previous(self):
        self.config(channel="stable")
        updater.update_now(self.root, log=lambda m: None)
        self.config(channel="beta")
        updater.update_now(self.root, log=lambda m: None)
        self.assertEqual(self.state()["previous"], "0.1.0")

    def test_off_without_a_channel(self):
        self.config()
        self.assertEqual(updater.update_now(self.root, log=lambda m: None), "0.0.0")
        self.assertEqual(self.gh.downloads, [])


if __name__ == "__main__":
    unittest.main()
