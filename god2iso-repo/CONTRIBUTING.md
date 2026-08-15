# Contributing

Thanks for wanting to help!  This project is small, focused and test-heavy —
please keep it that way.

## Ground rules

- **No network code.**  The tool is deliberately fully offline.  Any PR that
  adds a network-capable import (`socket`, `urllib`, `requests`, `http`,
  `asyncio`, …) will be rejected unless it's gated behind a clearly
  documented opt-in feature.
- **No key material / decryption.**  The project deliberately does not
  handle retail encryption.  Don't add crypto that bypasses DRM.
- **Tests must pass.**  Run the full suite before submitting:
  ```bash
  python tests/test_roundtrip.py
  python tests/test_safety.py
  python tests/test_gui.py
  python god2iso.py audit
  # optional, if you have the reference tool:
  EXTRACT_XISO=/path/to/extract-xiso python tests/test_cross_tool.py
  ```
- **pyflakes-clean.**  `python -m pyflakes god2iso.py gui.py util.py
  xcontent.py xsf.py xiso.py tests/*.py` must produce no output.
- **Legality.**  Only contribute features that help people process content
  they own or are licensed to use.

## Development setup

```bash
git clone <your-fork> && cd god2iso
python3 -m pip install pyflakes   # dev-only
python3 god2iso.py audit          # offline self-check
python3 god2iso.py                # GUI (or --wizard / CLI)
```

GUI work is verified headlessly with xvfb:
```bash
sudo apt-get install -y xvfb x11-apps imagemagick
xvfb-run -a -s "-screen 0 1280x900x24" python tests/gui_smoke.py
xvfb-run -a -s "-screen 0 1280x900x24" python tests/gui_e2e.py
```

## What to work on

Good first issues are usually tagged `good first issue`.  High-value areas:
parsing robustness, test coverage, documentation, and the Windows build
pipeline.  If you change behavior, add or update tests.

## Releasing (maintainers)

1. Bump `VERSION` in `god2iso.py` and `version_info.txt`.
2. `python god2iso.py audit > audit_result.txt`
3. Build with `build_windows.bat` (or the CI `build-exe` job).
4. Update `release/` (exe, sha256, zips) — the CI artifacts can be
   downloaded and published as a GitHub Release with the checksums.
5. Tag the release (e.g. `v1.2.1`) and publish release notes.
