import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import edged  # noqa: E402


def handler(client: str, host: str | None = "172.16.1.103:7781") -> edged.Handler:
    h = edged.Handler.__new__(edged.Handler)
    h.client_address = (client, 50000)
    h.headers = {"Host": host} if host else {}
    return h


class TestResolveLocal(unittest.TestCase):
    def test_remote_viewer_gets_hub_port(self):
        h = handler("192.168.1.50")
        self.assertEqual(h.resolve("http://127.0.0.1:7785/"), "http://172.16.1.103:7785/")
        self.assertEqual(h.resolve("http://localhost:8096/web/index.html?a=1"),
                         "http://172.16.1.103:8096/web/index.html?a=1")
        self.assertEqual(h.resolve("http://grafana.lan:3000/"), "http://grafana.lan:3000/")

    def test_ipv6_host(self):
        self.assertEqual(handler("fd00::5", "[fd00::1]:7781").resolve("http://127.0.0.1:7785/"),
                         "http://[fd00::1]:7785/")

    def test_local_viewer_gets_url_verbatim(self):
        self.assertEqual(handler("127.0.0.1").resolve("http://127.0.0.1:7785/"), "http://127.0.0.1:7785/")


if __name__ == "__main__":
    unittest.main()
