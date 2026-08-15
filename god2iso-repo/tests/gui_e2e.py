#!/usr/bin/env python3
"""End-to-end GUI test - run under a virtual display:

    xvfb-run -a python3 tests/gui_e2e.py

Builds the window, selects a real GOD package through the GUI's own
selection logic (including the multi-package dropdown), clicks Convert,
pumps the event loop until the worker finishes, verifies the green
success banner + output ISO, then exercises Extract and Rebuild.
Exits 0 on success.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import messagebox

import gui            # noqa: E402
import make_fake_god  # noqa: E402
import xiso           # noqa: E402

# modal dialogs would block headless: stub them out
messagebox.showinfo = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None


def pump(app, root, seconds=30):
    deadline = time.time() + seconds
    while app.busy and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    root.update()


tmp = tempfile.mkdtemp(prefix="gui_e2e_")
files = {"default.xex": b"X" * 16384, "media/a.bin": bytes(range(256)) * 128,
         "readme.txt": b"gui e2e"}
iso = os.path.join(tmp, "game.iso")
xiso.build_image(files, iso)

# two packages in one folder -> dropdown must appear
god = os.path.join(tmp, "GAMES", "53450810", "00007000")
os.makedirs(god)
live1, _ = make_fake_god.godify(open(iso, "rb").read(), god, flavor="A",
                                live_name="F32170AC3C4C9CA6EE6C",
                                part_cuts=[0xCC000])
god2 = os.path.join(tmp, "GAMES", "4156082F", "00007000")
os.makedirs(god2)
live2, _ = make_fake_god.godify(open(iso, "rb").read(), god2, flavor="B",
                                live_name="082DACEE274BCE0F6ED4")

root = tk.Tk()
app = gui.God2IsoApp(root)

# select the GAMES folder - two packages found -> dropdown
app._set_live(os.path.join(tmp, "GAMES"))
root.update()
assert app.pkg_combo.winfo_ismapped(), "package dropdown not shown"
assert len(app._pkg_paths) == 2, app._pkg_paths
print("dropdown OK, packages:", len(app._pkg_paths))

# pick a package via the dropdown (order is alphabetical by path)
app.pkg_combo.current(1)
app._on_pkg_chosen()
root.update()
expected = os.path.basename(app._pkg_paths[1])
assert os.path.basename(app.live_var.get()) == expected
print("dropdown selection OK:", app.live_var.get())

# summary card populated
assert "54455354" in app.info_var.get()
print("summary OK:", app.info_var.get()[:60])

# warnings: output exists + inside source folder (of the selected package)
out = os.path.join(os.path.dirname(app.live_var.get()), "x.iso")
open(out, "wb").write(b"old")
app.out_var.set(out)
app.force_var.set(False)
root.update()
assert "already exists" in app.warn_var.get()
assert "inside the game's folder" in app.warn_var.get()
print("warnings OK:", app.warn_var.get()[:60])

# force overwrite + convert
app.force_var.set(True)
out2 = os.path.join(tmp, "out.iso")
app.out_var.set(out2)
app._do_convert()
pump(app, root)
assert not app.busy, "conversion did not finish"
assert os.path.exists(out2), "output ISO not created"
assert xiso.find_default_xex(out2) == "default.xex", "default.xex missing"
# success banner visible
assert app.banner.winfo_ismapped(), "success banner not shown"
assert "FOUND" in app.status_var.get()
print("convert OK, banner shown, status:", app.status_var.get())

# --- extract tab ----------------------------------------------------------
exdir = os.path.join(tmp, "x")
app.ex_iso_var.set(out2)
app.ex_dir_var.set(exdir)
app._do_extract()
pump(app, root)
assert not app.busy, "extract did not finish"
assert os.path.isfile(os.path.join(exdir, "default.xex")), "extract missing files"
print("extract OK")

# --- rebuild tab -----------------------------------------------------------
rbout = os.path.join(tmp, "rebuilt.iso")
app.rb_iso_var.set(out2)
app.rb_out_var.set(rbout)
app._do_rebuild()
pump(app, root)
assert not app.busy, "rebuild did not finish"
assert os.path.isfile(rbout), "rebuild output missing"
assert xiso.find_default_xex(rbout) == "default.xex", "rebuild default.xex missing"
print("rebuild OK")

print("GUI_E2E_OK")
root.destroy()
sys.exit(0)
