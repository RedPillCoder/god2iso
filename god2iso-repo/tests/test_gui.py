#!/usr/bin/env python3
"""GUI tests for god2iso: helpers (no display needed) + headless fallback.

The full interactive GUI is exercised separately on a virtual display
(xvfb) via gui_e2e.py / gui_smoke.py.

Run:  python3 tests/test_gui.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui              # noqa: E402
import make_fake_god    # noqa: E402
import xiso             # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "god2iso.py")


def run_tool(*argv):
    return subprocess.run([sys.executable, TOOL, *argv],
                          capture_output=True, text=True)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gui_test_")
        files = {"default.xex": b"X" * 8192,
                 "media/tex_0000.bin": bytes(range(256)) * 64}
        iso = os.path.join(cls.tmp, "game.iso")
        xiso.build_image(files, iso)
        god = os.path.join(cls.tmp, "god")
        os.makedirs(god)
        cls.live, cls.parts = make_fake_god.godify(
            open(iso, "rb").read(), god, flavor="A")
        # a folder with multiple packages (combo case)
        god2 = os.path.join(cls.tmp, "god2")
        os.makedirs(god2)
        make_fake_god.godify(open(iso, "rb").read(), god2, flavor="B",
                             live_name="GAME0002.live")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_format_size(self):
        self.assertEqual(gui.format_size(0), "0 B")
        self.assertEqual(gui.format_size(1023), "1023 B")
        self.assertEqual(gui.format_size(2048), "2.0 KB")
        self.assertEqual(gui.format_size(7610167296), "7.1 GB")
        self.assertEqual(gui.format_size(None), "?")
        self.assertEqual(gui.format_size(-5), "?")

    def test_default_output_name(self):
        """The suggested output must NOT be inside the GOD source folder."""
        out = gui.default_output_name(self.live)
        self.assertTrue(out.endswith("TEST0001.iso"))
        self.assertNotEqual(os.path.dirname(out), os.path.dirname(self.live),
                            "output should not default into the source folder")

    def test_package_summary(self):
        s = gui.package_summary(self.live)
        self.assertEqual(s["title_id"], "54455354")
        self.assertEqual(s["media_id"], "12345678")
        self.assertEqual(s["parts"], 1)
        self.assertGreater(s["size"], 0)
        self.assertEqual(s["kind"], "Games on Demand")
        line = gui.summary_text(s)
        self.assertIn("54455354", line)
        self.assertIn("1 part file(s)", line)
        self.assertIn("Games on Demand", line)

    def test_info_line(self):
        line = gui.info_line(self.live)
        self.assertIn("54455354", line)
        self.assertIn("1 part file(s)", line)
        self.assertIn("Games on Demand", line)

    def test_find_multiple_packages(self):
        import god2iso
        lives = god2iso.find_live_files(self.tmp)
        self.assertGreaterEqual(len(lives), 2)

    def test_preflight_warnings(self):
        # empty inputs
        self.assertTrue(any("Choose a valid GOD" in w
                            for w in gui.preflight_warnings("", "x.iso")))
        self.assertTrue(any("Choose an output" in w
                            for w in gui.preflight_warnings(self.live, "")))
        # output == input
        ws = gui.preflight_warnings(self.live, self.live)
        self.assertTrue(any("header itself" in w for w in ws))
        # output inside source folder -> tip
        inside = os.path.join(os.path.dirname(self.live), "x.iso")
        ws = gui.preflight_warnings(self.live, inside)
        self.assertTrue(any("inside the game's folder" in w for w in ws))
        # existing output without force
        existing = os.path.join(self.tmp, "exists.iso")
        open(existing, "wb").write(b"x")
        ws = gui.preflight_warnings(self.live, existing)
        self.assertTrue(any("already exists" in w for w in ws))
        # with force -> no overwrite warning
        ws = gui.preflight_warnings(self.live, existing, force=True)
        self.assertFalse(any("already exists" in w for w in ws))

    def test_gui_flag_headless_graceful(self):
        """--gui without a display must give a clean message, no traceback."""
        if os.environ.get("DISPLAY"):
            self.skipTest("display present")
        rc = run_tool("--gui")
        self.assertEqual(rc.returncode, 1)
        self.assertIn("GUI unavailable", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_gui_module_imports_clean(self):
        """The gui module must import with no network-capable modules."""
        import util
        probs = util.audit_no_network(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(probs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
