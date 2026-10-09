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


class TestPickerSwipeDelete(unittest.TestCase):
    def test_swipe_uses_pointer_events_and_threshold(self):
        self.assertRegex(SHELL_PAGE, r"const SWIPE_PX = \d+;")
        for ev in ("pointerdown", "pointerup", "pointercancel"):
            self.assertIn(f"c.addEventListener('{ev}'", SHELL_PAGE)
        self.assertIn("dx >= SWIPE_PX && Math.abs(dx) > 2 * Math.abs(dy)", SHELL_PAGE)
        self.assertIn("touch-action:pan-y", SHELL_PAGE)  # vertical drags still scroll the column

    def test_delete_control_and_confirmation(self):
        self.assertIn("del.className = 'del'", SHELL_PAGE)
        self.assertIn('<div id="confirm" hidden>', SHELL_PAGE)
        self.assertIn('<span id="yes">yes</span><span id="no">no</span>', SHELL_PAGE)
        self.assertIn("fetch('/tiles/delete'", SHELL_PAGE)
        m = re.search(r"\$\('yes'\)\.onclick = async \(\) => \{(.+?)\n  \};", SHELL_PAGE, re.S)
        self.assertIsNotNone(m)
        self.assertIn("fillPicker()", m.group(1))

    def test_clock_never_deletable_and_swipe_does_not_pick(self):
        self.assertIn("if (t.deletable && t.url) swipeToDelete(c, t);", SHELL_PAGE)
        self.assertIn("if (c.dataset.swiped) {", SHELL_PAGE)


if __name__ == "__main__":
    unittest.main()
