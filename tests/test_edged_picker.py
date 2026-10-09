import re
import unittest
from pathlib import Path

SHELL_PAGE = (Path(__file__).parent.parent / "edged.py").read_text()


class TestPickerSubtitle(unittest.TestCase):
    def test_local_subtitle_text(self):
        self.assertIn("frakpanel connects to web server", SHELL_PAGE)
        # the old literal subtitle must be gone
        self.assertNotIn("'local'", SHELL_PAGE)

    def test_relay_subtitle_text(self):
        self.assertIn("webserver registers itself to frakpanel web server", SHELL_PAGE)

    def test_fill_picker_builds_expected_subtitle(self):
        m = re.search(
            r"s\.textContent = (t\.laptop \? .+);\n",
            SHELL_PAGE,
        )
        self.assertIsNotNone(m)
        expr = m.group(1)
        self.assertIn("webserver registers itself to frakpanel web server", expr)
        self.assertIn("t.laptop", expr)
        self.assertIn("away", expr)
        self.assertTrue(expr.rstrip().endswith("'frakpanel connects to web server'"))


if __name__ == "__main__":
    unittest.main()
