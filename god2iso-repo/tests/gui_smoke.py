#!/usr/bin/env python3
"""Interactive GUI smoke test - run under a virtual display:

    xvfb-run -a python3 tests/gui_smoke.py

Builds the full window, updates the event loop, verifies the title and
tab count, then exits 0.  Screenshots the window to gui_smoke.png for
inspection.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

OUT_PNG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "gui_smoke.png")


def main():
    print("building GUI...")
    root = __import__("tkinter").Tk()
    app = gui.God2IsoApp(root)
    root.update()
    root.geometry("+20+20")
    root.update_idletasks()

    # screenshot via ImageMagick `import` (X11)
    try:
        subprocess.run(["import", "-window", "root", OUT_PNG],
                       check=True, timeout=30)
        print("screenshot saved:", OUT_PNG)
    except Exception as e:                       # noqa: BLE001
        print("screenshot skipped:", e)

    title = root.title()
    tabs = app.nb.index("end")
    print("title:", title)
    print("tabs:", tabs)
    root.destroy()
    assert "god2iso" in title.lower(), title
    assert tabs == 3, tabs
    print("GUI_SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
