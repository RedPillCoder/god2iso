#!/usr/bin/env python3
"""gui.py - tkinter GUI for god2iso (launched by god2iso.py).

Pure standard library (tkinter ships with Python).  The GUI is a thin,
safe wrapper around the same core functions the CLI uses - identical
safety, non-destructive and offline guarantees, just with buttons.

Ease-of-use features:
  * one obvious way to pick a package (folder or .live file),
  * a package summary card (title/media/parts/size) once selected,
  * a dropdown when the chosen folder contains several packages,
  * friendly pre-flight warnings (output exists, output inside the game
    folder, missing paths) shown before you click Convert,
  * a clear green success banner with "default.xex FOUND", the output
    path, and Open-folder / Copy-path buttons,
  * live progress with phase text ("Reading part 3 of 45"),
  * a Recent-packages menu (paths are stored locally only, in gui.json;
    File -> Recent -> Clear recent files removes them).

Threading model: conversions run on a background thread; log lines,
progress and phase text are marshalled back to the UI through a queue
polled by the Tk event loop (tkinter is not thread-safe, so the worker
never touches widgets directly).
"""

import json
import os
import queue
import subprocess
import sys
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import util
import xcontent
import xiso

_core_module = None


def _core():
    global _core_module
    if _core_module is None:
        import god2iso
        _core_module = god2iso
    return _core_module


# ---------------------------------------------------------------------------
# pure helpers (unit-testable without a display)
# ---------------------------------------------------------------------------

def format_size(n):
    """Human-readable size: 7610167296 -> '7.1 GB'.  Returns '?' for
    missing or negative values."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%d B" % n if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "?"


def package_summary(live_path):
    """Dict describing a GOD package for the summary card."""
    info = xcontent.parse(open(live_path, "rb").read())
    try:
        parts = _core().find_part_files(live_path)
    except (FileNotFoundError, OSError):
        parts = []
    if info.content_type == xcontent.CONTENT_TYPE_GOD:
        kind = "Games on Demand"
    elif info.content_type == xcontent.CONTENT_TYPE_XBOX_ORIGINAL:
        kind = "Xbox Original"
    else:
        kind = "content type 0x%04X" % info.content_type
    return {
        "path": live_path,
        "title_id": info.title_id_hex,
        "media_id": "%08X" % info.media_id,
        "parts": len(parts),
        "size": sum(os.path.getsize(p) for p in parts),
        "kind": kind,
    }


def summary_text(s):
    return ("Title %s   Media %s   %d part file(s)   %s   (%s)"
            % (s["title_id"], s["media_id"], s["parts"], s["kind"],
               format_size(s["size"])))


def info_line(live_path):
    """One-line summary (kept for compatibility/tests)."""
    return summary_text(package_summary(live_path))


def find_packages(path):
    return _core().find_live_files(path)


def preflight_warnings(live, out, force=False):
    """Friendly warnings shown before converting.  Returns a list of
    strings (empty = all good)."""
    ws = []
    if not live or not os.path.isfile(live):
        ws.append("Choose a valid GOD package first.")
    if not out:
        ws.append("Choose an output ISO path.")
    if live and out:
        a_out = os.path.abspath(out)
        a_live = os.path.abspath(live)
        if os.path.normcase(a_out) == os.path.normcase(a_live):
            ws.append("The output path is the package header itself - "
                      "pick another name.")
        src = os.path.dirname(a_live)
        if os.path.normcase(a_out).startswith(os.path.normcase(src)):
            ws.append("Tip: the output is inside the game's folder - "
                      "save somewhere else (e.g. your Desktop).")
        if os.path.exists(out) and not force:
            ws.append("The output file already exists - tick "
                      "'Overwrite existing' to replace it.")
    return ws


def config_path():
    """Local (offline) config file for recent paths."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "god2iso")
    else:
        folder = os.path.join(os.path.expanduser("~"), ".config", "god2iso")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.expanduser("~")
    return os.path.join(folder, "gui.json")


def load_recent():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("recent", [])), data.get("last_outdir", "")
    except Exception:                                   # noqa: BLE001
        return [], ""


def save_recent(recent, last_outdir=""):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump({"recent": list(recent)[:8],
                       "last_outdir": last_outdir}, f)
    except Exception:                                   # noqa: BLE001
        pass


def default_output_name(live_path):
    """A sensible suggested output path for a GOD package.

    Prefers a writable, non-source location: the user's Desktop (or home
    dir on non-Windows), falling back to the source folder."""
    candidates = []
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(
                    0, 0x0000, None, 0, buf) == 0:      # CSIDL_DESKTOPDIRECTORY
                candidates.append(buf.value)
        except Exception:                               # noqa: BLE001
            pass
        candidates.append(os.path.expanduser("~"))
    else:
        candidates.append(os.path.expanduser("~"))
    stem = os.path.splitext(os.path.basename(live_path))[0]
    for base in candidates:
        if os.path.isdir(base):
            return os.path.join(base, stem + ".iso")
    return os.path.join(
        os.path.dirname(os.path.abspath(live_path)), stem + ".iso")


def open_in_file_manager(path):
    """Open the folder containing *path* in the OS file manager."""
    folder = os.path.dirname(os.path.abspath(path))
    if sys.platform == "win32":
        os.startfile(folder)                            # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


# ---------------------------------------------------------------------------
# the application
# ---------------------------------------------------------------------------

class God2IsoApp:
    TITLE = "god2iso - Xbox 360 GOD to ISO converter"

    def __init__(self, root):
        self.root = root
        self.root.title(self.TITLE)
        self.root.geometry("820x660")
        self.root.minsize(740, 580)
        self._set_icon()
        self.busy = False
        self.q = queue.Queue()
        self.last_out = None
        self._recent, self._last_outdir = load_recent()

        # variables first - the tab builders bind to them
        self.live_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.trim_var = tk.BooleanVar(value=True)
        self.fix_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)
        self.sha_var = tk.BooleanVar(value=True)
        self.verify_var = tk.BooleanVar(value=True)
        self.info_var = tk.StringVar(value="No package selected yet.")

        self._build_menu()
        self._build_tabs()

        # live warning updates
        self.live_var.trace_add("write", lambda *_: self._update_warnings())
        self.out_var.trace_add("write", lambda *_: self._update_warnings())
        self.force_var.trace_add("write", lambda *_: self._update_warnings())

    # -- setup ---------------------------------------------------------------

    def _set_icon(self):
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, "assets", "god2iso.ico")
        if os.name == "nt" and os.path.exists(ico):
            try:
                self.root.iconbitmap(ico)
            except tk.TclError:
                pass

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open GOD package...",
                             command=self._pick_live_folder)
        filemenu.add_command(label="Open .live file...",
                             command=self._pick_live_dialog)
        self.recent_menu = tk.Menu(menubar, tearoff=0)
        filemenu.add_cascade(label="Recent packages", menu=self.recent_menu)
        filemenu.add_command(label="Clear recent packages",
                             command=self._clear_recent)
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=filemenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Quick guide", command=self._show_guide)
        helpmenu.add_command(label="Offline audit...", command=self._show_audit)
        helpmenu.add_command(label="About...", command=self._show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.root.config(menu=menubar)
        self._refresh_recent_menu()

    def _build_tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_convert_tab()
        self._build_extract_tab()
        self._build_rebuild_tab()

    def _scrolled_log(self, parent, height=7):
        frame = ttk.Frame(parent)
        txt = tk.Text(frame, height=height, wrap="none", state="disabled",
                      font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        frame.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        return txt

    def _log_write(self, txt, msg):
        txt.configure(state="normal")
        txt.insert("end", msg + "\n")
        txt.see("end")
        txt.configure(state="disabled")

    # -- Convert tab ----------------------------------------------------------

    def _build_convert_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  Convert  ")
        pad = {"padx": 6, "pady": 3}

        # step 1 - pick the package
        r1 = ttk.Frame(tab)
        r1.pack(fill="x", padx=8, pady=(10, 0))
        ttk.Label(r1, text="1.  Choose your game folder",
                  font=("TkDefaultFont", 10, "bold")).pack(side="left",
                                                           **pad)
        self.live_entry = ttk.Entry(r1, textvariable=self.live_var)
        self.live_entry.pack(side="left", fill="x", expand=True, **pad)
        ttk.Button(r1, text="Browse folder...",
                   command=self._pick_live_folder).pack(side="left", **pad)
        ttk.Button(r1, text=".live file...",
                   command=self._pick_live_dialog).pack(side="left", **pad)

        # package summary card
        self.card = ttk.LabelFrame(tab, text="Package", padding=6)
        self.card.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(self.card, textvariable=self.info_var,
                  foreground="#0066cc", wraplength=720).pack(anchor="w")
        self.pkg_combo = ttk.Combobox(self.card, state="readonly",
                                      width=60)
        self.pkg_combo.bind("<<ComboboxSelected>>", self._on_pkg_chosen)

        # step 2 - output
        r2 = ttk.Frame(tab)
        r2.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(r2, text="2.  Output ISO",
                  font=("TkDefaultFont", 10, "bold")).pack(side="left",
                                                           **pad)
        ttk.Entry(r2, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, **pad)
        ttk.Button(r2, text="Browse...",
                   command=self._pick_out_dialog).pack(side="left", **pad)

        # pre-flight warnings
        self.warn_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.warn_var, foreground="#cc0000",
                  wraplength=760, justify="left").pack(
            fill="x", padx=12, pady=(2, 0))

        # step 3 - options + convert
        r3 = ttk.Frame(tab)
        r3.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Checkbutton(r3, text="Overwrite existing (--force)",
                        variable=self.force_var).pack(side="left", **pad)
        ttk.Checkbutton(r3, text="Trim padding",
                        variable=self.trim_var).pack(side="left", **pad)
        ttk.Checkbutton(r3, text="Show SHA-256",
                        variable=self.sha_var).pack(side="left", **pad)
        ttk.Checkbutton(r3, text="Deep verify (MHT)",
                        variable=self.verify_var).pack(side="left", **pad)
        ttk.Checkbutton(r3, text="Fix header (advanced)",
                        variable=self.fix_var).pack(side="left", **pad)
        self.convert_btn = ttk.Button(r3, text="Convert", width=14,
                                      command=self._do_convert)
        self.convert_btn.pack(side="left", **pad)

        r4 = ttk.Frame(tab)
        r4.pack(fill="x", padx=8, pady=(2, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(r4, textvariable=self.status_var,
                  foreground="#333333").pack(side="left", **pad)
        self.open_btn = ttk.Button(r4, text="Open output folder",
                                   command=lambda: self._open_last(),
                                   state="disabled")
        self.open_btn.pack(side="left", **pad)

        self.progress = ttk.Progressbar(tab, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=(6, 0))
        self.progress_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.progress_var,
                  foreground="#333333").pack(anchor="w", padx=12)

        # success banner (hidden until a conversion verifies)
        self.banner = tk.Frame(tab, bg="#d9f2dc", padx=10, pady=8)
        tk.Label(self.banner, text="✓ Conversion verified - "
                                   "default.xex FOUND",
                 bg="#d9f2dc", fg="#0a5c1e",
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.banner_path = tk.Label(self.banner, text="", bg="#d9f2dc",
                                    fg="#0a5c1e", justify="left",
                                    wraplength=740)
        self.banner_path.pack(anchor="w")
        brow = tk.Frame(self.banner, bg="#d9f2dc")
        brow.pack(anchor="w", pady=(4, 0))
        tk.Button(brow, text="Open folder",
                  command=lambda: self._open_last()).pack(side="left",
                                                          padx=(0, 6))
        tk.Button(brow, text="Copy path",
                  command=self._copy_last_path).pack(side="left")

        self.convert_log = self._scrolled_log(tab, height=6)

    def _refresh_recent_menu(self):
        self.recent_menu.delete(0, "end")
        if not self._recent:
            self.recent_menu.add_command(label="(none)", state="disabled")
        for p in self._recent:
            self.recent_menu.add_command(
                label=p, command=lambda p=p: self._set_live(p))

    def _clear_recent(self):
        self._recent = []
        save_recent(self._recent, self._last_outdir)
        self._refresh_recent_menu()

    def _remember(self, path):
        if path in self._recent:
            self._recent.remove(path)
        self._recent.insert(0, path)
        save_recent(self._recent, self._last_outdir)
        self._refresh_recent_menu()

    def _pick_live_dialog(self):
        path = filedialog.askopenfilename(
            title="Select a GOD package (.live file or header)",
            filetypes=[("Xbox 360 GOD package", "*.live"),
                       ("GOD header (no extension)", "*.*"),
                       ("All files", "*.*")])
        if path:
            self._set_live(path)

    def _pick_live_folder(self):
        path = filedialog.askdirectory(
            title="Select the game folder (TitleID or 00007000 folder)")
        if path:
            self._set_live(path)

    def _set_live(self, path):
        self.live_var.set(path)
        lives = find_packages(path)
        if not lives:
            self.info_var.set("No GOD package found there.  Pick the game "
                              "folder (e.g. ...\\4156082F) or its header "
                              "file.")
            self.pkg_combo.pack_forget()
            self._update_warnings()
            return
        self._remember(lives[0])
        if len(lives) > 1:
            # several packages in one folder: offer a dropdown
            labels = []
            for l in lives:
                try:
                    labels.append(summary_text(package_summary(l)))
                except Exception:                       # noqa: BLE001
                    labels.append(l)
            self.pkg_combo["values"] = labels
            self.pkg_combo.current(0)
            self.pkg_combo.pack(anchor="w", pady=(4, 0), fill="x")
            self._pkg_paths = lives
        else:
            self.pkg_combo.pack_forget()
            self._pkg_paths = lives
        self._apply_pkg(lives[0])

    def _on_pkg_chosen(self, _event=None):
        idx = self.pkg_combo.current()
        if 0 <= idx < len(self._pkg_paths):
            self._apply_pkg(self._pkg_paths[idx])

    def _apply_pkg(self, live):
        self.live_var.set(live)
        try:
            self.info_var.set(summary_text(package_summary(live)))
        except Exception as e:                          # noqa: BLE001
            self.info_var.set("Could not read package: %s" % e)
        if not self.out_var.get():
            out = default_output_name(live)
            if self._last_outdir and os.path.isdir(self._last_outdir):
                out = os.path.join(self._last_outdir,
                                   os.path.basename(out))
            self.out_var.set(out)
        self._update_warnings()

    def _pick_out_dialog(self):
        path = filedialog.asksaveasfilename(
            title="Save ISO as", defaultextension=".iso",
            initialfile=os.path.basename(self.out_var.get() or "game.iso"),
            filetypes=[("ISO image", "*.iso"), ("All files", "*.*")])
        if path:
            self.out_var.set(path)

    def _update_warnings(self):
        live = self.live_var.get().strip().strip('"')
        out = self.out_var.get().strip().strip('"')
        ws = preflight_warnings(live, out, self.force_var.get())
        self.warn_var.set("\n".join(ws))

    def _do_convert(self):
        if self.busy:
            return
        live = self.live_var.get().strip().strip('"')
        out = self.out_var.get().strip().strip('"')
        hard = [w for w in preflight_warnings(live, out, True)
                if w.startswith(("Choose", "The output path"))]
        if hard:
            messagebox.showerror("god2iso", "\n".join(hard))
            return
        self.last_out = out
        self.banner.pack_forget()
        self.progress_var.set("")
        self._log_write(self.convert_log,
                        "== converting %s" % os.path.basename(live))
        core = _core()
        # read all widget values NOW - Tk Variables must not be touched
        # from the worker thread
        trim = self.trim_var.get()
        fix = self.fix_var.get()
        force = self.force_var.get()
        sha = self.sha_var.get()
        verify = self.verify_var.get()

        def phase(msg):
            self.q.put(("phase", msg))

        self._run(
            lambda log, prog: core.convert(
                live, out,
                trim=trim, fix=fix, force=force, sha256=sha,
                verify=verify,
                log=log, progress_cb=prog, phase_cb=phase),
            log_txt=self.convert_log,
            status_ok=lambda: "Conversion complete - default.xex FOUND",
            status_missing=lambda: "Converted, but default.xex was NOT found "
                                   "(check the log)",
            status_bad=lambda: "Output does not look like a valid Xbox ISO "
                               "(input may be encrypted - see log)")

    # -- Extract tab -----------------------------------------------------------

    def _build_extract_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  Extract  ")
        pad = {"padx": 6, "pady": 3}

        r1 = ttk.Frame(tab); r1.pack(fill="x", padx=8, pady=(10, 0))
        ttk.Label(r1, text="ISO image:").pack(side="left", **pad)
        self.ex_iso_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.ex_iso_var).pack(
            side="left", fill="x", expand=True, **pad)
        ttk.Button(r1, text="Browse...", command=self._pick_ex_iso).pack(
            side="left", **pad)

        r2 = ttk.Frame(tab); r2.pack(fill="x", padx=8)
        ttk.Label(r2, text="Output folder:").pack(side="left", **pad)
        self.ex_dir_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.ex_dir_var).pack(
            side="left", fill="x", expand=True, **pad)
        ttk.Button(r2, text="Browse...", command=self._pick_ex_dir).pack(
            side="left", **pad)

        r3 = ttk.Frame(tab); r3.pack(fill="x", padx=8, pady=6)
        self.ex_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text="Overwrite existing files (--force)",
                        variable=self.ex_force_var).pack(side="left", **pad)
        self.extract_btn = ttk.Button(r3, text="Extract", width=14,
                                      command=self._do_extract)
        self.extract_btn.pack(side="left", **pad)
        self.ex_status = tk.StringVar(value="")
        ttk.Label(r3, textvariable=self.ex_status,
                  foreground="#333333").pack(side="left", **pad)

        self.extract_log = self._scrolled_log(tab)

    def _pick_ex_iso(self):
        p = filedialog.askopenfilename(title="Select an Xbox ISO",
                                       filetypes=[("ISO image", "*.iso"),
                                                  ("All files", "*.*")])
        if p:
            self.ex_iso_var.set(p)

    def _pick_ex_dir(self):
        p = filedialog.askdirectory(title="Select the extraction folder")
        if p:
            self.ex_dir_var.set(p)

    def _do_extract(self):
        if self.busy:
            return
        iso = self.ex_iso_var.get().strip().strip('"')
        outdir = self.ex_dir_var.get().strip().strip('"')
        if not iso or not os.path.isfile(iso):
            messagebox.showerror("god2iso", "Please choose a valid ISO file.")
            return
        if not outdir:
            messagebox.showerror("god2iso", "Please choose an output folder.")
            return
        self._log_write(self.extract_log, "== extracting %s"
                        % os.path.basename(iso))
        ex_force = self.ex_force_var.get()     # read before threading
        self.ex_status.set("Extracting...")
        self._run(
            lambda log, prog: len(xiso.extract_image(
                iso, outdir, force=ex_force,
                lseek_offset=xiso.partition_offset(iso))),
            log_txt=self.extract_log,
            status_ok=lambda: "Extraction complete",
            status_missing=lambda: "Extraction reported no files",
            status_bad=lambda: "Extraction failed - see log",
            done_msg=lambda n: "Extracted %d file(s) to %s" % (n, outdir),
            status_label=self.ex_status)

    # -- Rebuild tab -----------------------------------------------------------

    def _build_rebuild_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  Rebuild  ")
        pad = {"padx": 6, "pady": 3}

        r1 = ttk.Frame(tab); r1.pack(fill="x", padx=8, pady=(10, 0))
        ttk.Label(r1, text="ISO image:").pack(side="left", **pad)
        self.rb_iso_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.rb_iso_var).pack(
            side="left", fill="x", expand=True, **pad)
        ttk.Button(r1, text="Browse...", command=self._pick_rb_iso).pack(
            side="left", **pad)

        r2 = ttk.Frame(tab); r2.pack(fill="x", padx=8)
        ttk.Label(r2, text="Output ISO:").pack(side="left", **pad)
        self.rb_out_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.rb_out_var).pack(
            side="left", fill="x", expand=True, **pad)
        ttk.Button(r2, text="Browse...", command=self._pick_rb_out).pack(
            side="left", **pad)

        r3 = ttk.Frame(tab); r3.pack(fill="x", padx=8, pady=6)
        self.rb_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text="Overwrite existing (--force)",
                        variable=self.rb_force_var).pack(side="left", **pad)
        self.rb_btn = ttk.Button(r3, text="Rebuild", width=14,
                                 command=self._do_rebuild)
        self.rb_btn.pack(side="left", **pad)
        self.rb_status = tk.StringVar(value="")
        ttk.Label(r3, textvariable=self.rb_status,
                  foreground="#333333").pack(side="left", **pad)

        self.rebuild_log = self._scrolled_log(tab)

    def _pick_rb_iso(self):
        p = filedialog.askopenfilename(title="Select an Xbox ISO",
                                       filetypes=[("ISO image", "*.iso"),
                                                  ("All files", "*.*")])
        if p:
            self.rb_iso_var.set(p)
            self.rb_out_var.set(os.path.splitext(p)[0] + ".rebuilt.iso")

    def _pick_rb_out(self):
        p = filedialog.asksaveasfilename(
            title="Save rebuilt ISO as", defaultextension=".iso",
            initialfile=os.path.basename(self.rb_out_var.get() or "rebuilt.iso"),
            filetypes=[("ISO image", "*.iso"), ("All files", "*.*")])
        if p:
            self.rb_out_var.set(p)

    def _do_rebuild(self):
        if self.busy:
            return
        iso = self.rb_iso_var.get().strip().strip('"')
        out = self.rb_out_var.get().strip().strip('"')
        if not iso or not os.path.isfile(iso):
            messagebox.showerror("god2iso", "Please choose a valid ISO file.")
            return
        if not out:
            messagebox.showerror("god2iso", "Please choose an output ISO path.")
            return
        self.last_out = out
        self._log_write(self.rebuild_log, "== rebuilding %s"
                        % os.path.basename(iso))
        core = _core()
        rb_force = self.rb_force_var.get()     # read before threading
        self.rb_status.set("Rebuilding...")
        self._run(
            lambda log, prog: core.rebuild_image(iso, out,
                                                 force=rb_force,
                                                 log=log),
            log_txt=self.rebuild_log,
            status_ok=lambda: "Rebuild complete - default.xex FOUND",
            status_missing=lambda: "Rebuilt, but default.xex was NOT found",
            status_bad=lambda: "Rebuild failed - see log",
            status_label=self.rb_status)

    # -- threaded runner -------------------------------------------------------

    def _run(self, worker, log_txt, status_ok, status_missing, status_bad,
             done_msg=None, status_label=None):
        self.busy = True
        self.q = queue.Queue()
        self.progress.configure(value=0)
        self._set_busy_state(True)
        self.status_var.set("Working...")
        self.progress_var.set("Starting...")
        self._status_texts = (status_ok, status_missing, status_bad)

        def target():
            try:
                rc = worker(self._qlog(log_txt), self._qprog)
                self.q.put(("done", rc))
            except Exception as e:                      # noqa: BLE001
                self.q.put(("error", "%s: %s" % (type(e).__name__, e)))

        threading.Thread(target=target, daemon=True).start()
        self.root.after(60, self._poll)

    def _qlog(self, txt):
        def emit(msg):
            self.q.put(("log", (txt, msg)))
        return emit

    def _qprog(self, done, total):
        frac = (done / total) if total else 0.0
        self.q.put(("progress", min(max(frac, 0.0), 1.0)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    txt, msg = payload
                    self._log_write(txt, msg)
                elif kind == "progress":
                    self.progress.configure(value=payload * 100)
                elif kind == "phase":
                    self.progress_var.set(payload)
                elif kind == "done":
                    self._finish(payload)
                    return
                elif kind == "error":
                    self._log_write(self._active_log(), "ERROR: %s" % payload)
                    self.status_var.set("Failed - see log")
                    self.progress_var.set("")
                    self._set_busy_state(False)
                    self.busy = False
                    messagebox.showerror("god2iso", payload)
                    return
        except queue.Empty:
            pass
        if self.busy:
            self.root.after(60, self._poll)

    def _active_log(self):
        tab = self.nb.select()
        if tab:
            w = self.nb.nametowidget(tab)
            for child in w.winfo_children():
                if isinstance(child, tk.Text):
                    return child
        return self.convert_log

    def _finish(self, rc):
        self.busy = False
        self._set_busy_state(False)
        ok_txt, missing_txt, bad_txt = self._status_texts
        if rc == 0:
            self.status_var.set(ok_txt())
            self.open_btn.configure(state="normal")
            self.progress_var.set("Done.")
            if self.last_out:
                self.banner_path.configure(text=self.last_out)
                self.banner.pack(fill="x", padx=8, pady=(4, 0),
                                 before=self.convert_log)
                self._remember(os.path.dirname(self.last_out))
        elif rc == 2:
            self.status_var.set(missing_txt())
            self.open_btn.configure(state="normal")
            self.progress_var.set("Finished with warnings - see log.")
        else:
            self.status_var.set(bad_txt())
            self.progress_var.set("")

    def _set_busy_state(self, busy):
        state = "disabled" if busy else "normal"
        for w in (self.convert_btn, self.extract_btn, self.rb_btn):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        try:
            self.live_entry.configure(state=state)
        except tk.TclError:
            pass

    def _open_last(self):
        if self.last_out and os.path.exists(self.last_out):
            open_in_file_manager(self.last_out)

    def _copy_last_path(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.last_out or "")
        except tk.TclError:
            pass

    # -- Help dialogs ----------------------------------------------------------

    def _show_guide(self):
        messagebox.showinfo(
            "Quick guide",
            "Convert a game:\n"
            "  1. Click 'Browse folder...' and pick the game folder\n"
            "     (the one with the 00007000 folder inside), or pick\n"
            "     the .live/header file directly.\n"
            "  2. Choose where to save the ISO (Desktop by default).\n"
            "  3. Click Convert and wait.\n\n"
            "Success = the green banner shows 'default.xex FOUND'.\n"
            "The output ISO can be used in emulators (e.g. Xenia) or\n"
            "with disc tools.\n\n"
            "Extract = unpack an ISO's files to a folder.\n"
            "Rebuild = rebuild a clean ISO from an existing one.\n\n"
            "The tool is fully offline and never modifies your game\n"
            "files.")

    def _show_audit(self):
        core = _core()
        try:
            lines = core.audit_lines()
        except Exception as e:                          # noqa: BLE001
            lines = ["audit failed: %s" % e]
        messagebox.showinfo("Offline audit", "\n".join(lines))

    def _show_about(self):
        core = _core()
        exe = getattr(sys, "executable", "")
        h = ""
        if getattr(sys, "frozen", False) and os.path.isfile(exe):
            try:
                h = "\nSHA-256: " + util.sha256_file(exe)
            except OSError:
                pass
        messagebox.showinfo(
            "About god2iso",
            "god2iso v%s\n\nXbox 360 GOD to ISO converter.\n\n"
            "- Fully offline (no network access, no telemetry)\n"
            "- Non-destructive (atomic writes, never overwrites without "
            "--force)\n"
            "- Recent packages are remembered locally (File -> Recent;\n"
            "  stored in gui.json - 'Clear recent packages' removes them)\n"
            "- MIT licensed - rebuildable from source\n"
            "Uses only content you own or are licensed to process.\n"
            "Help -> Offline audit verifies this build." % core.VERSION + h)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> int:
    root = tk.Tk()
    God2IsoApp(root)
    root.mainloop()
    return 0


def smoke() -> str:
    """Headless-friendly self-test: build the UI, return the window title."""
    root = tk.Tk()
    app = God2IsoApp(root)
    root.update()
    title = root.title()
    n_tabs = app.nb.index("end")
    root.destroy()
    return "%s|tabs=%d" % (title, n_tabs)


if __name__ == "__main__":
    sys.exit(main())
