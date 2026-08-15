# Changelog

## 1.2.1 — 2026-08-14
- Fix: `format_size()` mishandled negative values (GUI edge case).
- GUI: pre-flight warnings, package summary card, multi-package dropdown,
  green success banner (v1.2.0 feature set), recent-packages menu.
- Internal: dead-code removal; pyflakes-clean.

## 1.2.0 — 2026-08-14
- GUI overhaul for ease of use: guided numbered steps, package summary
  (Title ID / Media ID / parts / size), dropdown when a folder holds
  multiple packages, live warnings, phase text ("Reading part 3 of 45"),
  green "default.xex FOUND" banner with Open-folder / Copy-path.
- `File → Recent packages` (stored locally in `gui.json`).

## 1.1.2 — 2026-08-13
- Robust XDVDFS detection: scans the whole output for the volume descriptor
  (supports Redump-style XGD2/XGD3 images where the game partition starts at
  an offset); `list`/`extract`/`rebuild` auto-detect the partition offset.
- `atomic_output` falls back to the system temp dir when the destination
  folder is unwritable (OneDrive / long paths / permissions).
- GUI default output goes to Desktop/home, never inside the game folder.
- Auto-detect extension-less GOD headers (classic iso2god naming:
  `<MediaID>` + `<MediaID>.data`).

## 1.1.0 — 2026-08-13
- GUI (tkinter): Convert / Extract / Rebuild tabs.
- Console auto-hide when the exe is double-clicked.

## 1.0.0 — 2026-08-13
- Initial release: offline, non-destructive GOD → ISO converter
  (CLI + wizard), XDVDFS reader/writer, safety hardening.
