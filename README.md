# god2iso.py — safe, offline, cross-platform Xbox 360 GOD → ISO converter

A clean-room Python 3 implementation of the GOD → ISO conversion performed by
the classic **GOD2ISO v1.0.5** tool (raburton,
[github.com/raburton/god2iso](https://github.com/raburton/god2iso), CC-BY-SA-4.0),
plus XDVDFS reading/rebuilding in the style of **extract-xiso**
([github.com/XboxDev/extract-xiso](https://github.com/XboxDev/extract-xiso)).
The format behavior was studied from those open-source implementations and
cross-verified against **iso2god-rs** (the modern producer,
[github.com/iliazeus/iso2god-rs](https://github.com/iliazeus/iso2god-rs)),
ConsoleMods wiki, free60/arkem format references and xboxdevwiki.

## Windows: just run the .exe — nothing to install

**`release/god2iso.exe`** is a standalone Windows 64-bit executable
(≈ 10 MB) built with PyInstaller.  Windows users need **no Python, no
dependencies, no admin rights**.

**Double-click it and you get the GUI** — a simple three-tab window:

* **Convert** — guided, numbered steps:
  1. **Choose your game folder** (or `.live`/header file) — the package is
     auto-detected and a summary card shows Title ID, Media ID, part count
     and size; if the folder holds several packages, a dropdown lets you
     pick one.
  2. **Output ISO** — defaults to your Desktop (never inside the game's
     folder).
  3. Friendly pre-flight warnings appear before you click Convert (output
     exists, output inside the game folder, missing paths), then **Convert**
     with a live progress bar and phase text ("Reading part 3 of 45").
  Success shows a green banner — **"✓ Conversion verified - default.xex
  FOUND"** — with the output path plus **Open folder** and **Copy path**
  buttons.
* **Extract** — unpack an Xbox ISO to a folder.
* **Rebuild** — rebuild a clean XISO from an ISO.

`File → Recent packages` remembers what you last opened (stored locally in
`gui.json` only; "Clear recent packages" removes it).  Everything runs in
the background thread with the log streamed into the window; the same
non-destructive and offline guarantees apply (nothing is overwritten
without ticking "Overwrite", inputs are never modified, and
`Help → Offline audit` verifies the exe cannot phone home).

The GUI is a thin wrapper over the exact same engine as the CLI — both are
one executable:

```
god2iso.exe                        # GUI (double-click, or from a terminal)
god2iso.exe --wizard               # classic console wizard
god2iso.exe convert <path-to-.live-or-folder>
god2iso.exe audit                  # verify the exe is offline
```

When launched from a terminal, the console stays; when double-clicked, the
console window is hidden automatically and only the GUI is shown.

### Windows compatibility

* **64-bit Windows 10 / 11** (the runtime, Python 3.12, supports Windows 10
  and newer per [PEP 11](https://peps.python.org/pep-0011/); Windows 8.1/7
  are end-of-life and unsupported).  The embedded manifest declares
  `longPathAware` (deep paths work), `asInvoker` (no admin elevation), and
  supported-OS entries for Windows 7–11.
* Two build variants:
  * **`god2iso.exe`** — single-file.  Needs ~30 MB of free temp space while
    it unpacks itself at launch (standard PyInstaller onefile behavior).
  * **`god2iso-windows-onedir-1.0.0.zip`** — folder-based (`god2iso\god2iso.exe`
    + `_internal\`).  No self-extraction at all, so it runs even on
    nearly-full disks and tends to trip fewer antivirus heuristics.  Same
    code, same behavior, byte-identical output.
* If Windows Defender or another AV quarantines the exe, see the
  "antivirus false positives" section below.

**Safety of the packaged exe — verified, not assumed:**

* Built from this exact source, no UPX, console app, Windows 64-bit PE.
* **Structurally offline**: the build excludes every network-capable Python
  module from the bundle (`socket`, `ssl`, `http.client`, `urllib.request`,
  `email`, `ftplib`, `smtplib`, `telnetlib`, `xmlrpc`, `aiohttp`, …) — the
  archive contents were inspected and contain none of them.
* **Self-verifying**: `god2iso.exe audit` prints the embedded source-audit
  proof (generated from the exact code that was packaged) and scans every
  module imported at runtime for network capability — it passes.
* **Same non-destructive/security guarantees** as the Python tool (atomic
  writes, no overwrites without `--force`, path-traversal and symlink
  defenses, encrypted-data detection).  The packaged exe was executed and
  tested under Wine: conversions are **byte-identical** to the source build,
  extraction matches the originals, and every safety behavior holds.
* SHA-256: see `release/god2iso.exe.sha256` (also inside the zip).
  Verify it after download: `certutil -hashfile god2iso.exe SHA256`.

**Rebuild it yourself (transparency):** `build_windows.bat` runs the source
audit, builds the exe with PyInstaller from `god2iso.spec` (network modules
excluded), and prints the SHA-256 — so you can reproduce the artifact from
source and compare.

> Windows may show a SmartScreen "unknown publisher" prompt: the exe is
> unsigned (no code-signing certificate is used).  Verify the SHA-256 above
> and/or rebuild with `build_windows.bat` instead of trusting the binary.

### Why antivirus engines may flag the .exe (and what to do)

If you scan the exe on VirusTotal, a handful of engines may report it —
commonly `Trojan:Win32/Wacatac.B!ml` (Microsoft), `Static AI - Suspicious
PE` (SentinelOne), `Malicious (high confidence)` (Elastic/DeepInstinct) or
similar generic names.  This is a **well-known false-positive pattern for
PyInstaller-packaged executables**, not evidence of malware:

* The `!ml` / "Static ML" / "Static AI" suffixes mean the verdict comes from
  a **machine-learning heuristic**, not from matching a known malware
  signature.  None of the engines agree on a family name — the hallmark of a
  heuristic flag, not a real infection.
* PyInstaller's single-file mode produces a **self-extracting executable**
  (small bootloader + embedded compressed archive).  Structurally, real
  malware droppers also self-extract, so heuristics score it suspicious.
* The file is **unsigned and new** (low reputation), and PyInstaller's
  bootloader is generic and shared — both factors push ML scores up.  This
  affects essentially *every* fresh PyInstaller build ([PyInstaller issue
  #5848](https://github.com/pyinstaller/pyinstaller/issues/5848),
  [community threads](https://www.reddit.com/r/learnpython/comments/1d2pamz/pyinstaller_exe_falsely_flagged_as_wacatacb_virus/)).
* The `Wacatac.B!ml` name specifically is Microsoft's most notorious
  false-positive label for compiled/packaged tools — Microsoft routinely
  confirms such submissions as false positives
  ([discussion](https://superuser.com/questions/1829864/trojanwin32-wacatac-bml-found-in-c-extend-deleted)).

**Evidence this exe is exactly what we say it is:**

* It was built from the auditable source in this folder; `build_windows.bat`
  reproduces it and prints the SHA-256 — rebuild and compare.
* The bundle contents were inspected: it contains the Python standard
  library plus only our five modules (`god2iso`, `util`, `xcontent`, `xsf`,
  `xiso`) and the audit proof.  **No network-capable modules** (`socket`,
  `ssl`, `http.client`, `urllib.request`, `email`, `ftplib`, `smtplib`,
  `xmlrpc`, `aiohttp`) are present at all — the exe structurally cannot
  phone home.
* `god2iso.exe audit` verifies the offline guarantee at runtime.
* The exe was executed and tested: conversions are byte-identical to the
  source build, all safety behaviors hold, and the full test suite
  (45 tests incl. reference-tool cross-validation) passes.
* No UPX, no code from the web, no third-party dependencies.

**What you can do if an engine flags it:**

1. **Verify** the file: `certutil -hashfile god2iso.exe SHA256` must equal
   `release/god2iso.exe.sha256`.
2. **Rebuild from source** with `build_windows.bat` — if your rebuild hashes
   the same, it is provably this code.
3. **Submit a false-positive report** — this is the normal fix and vendors
   act on it:
   * Microsoft: <https://www.microsoft.com/en-us/wdsi/filesubmission>
     (aka.ms/avsubmit)
   * Other vendors (DeepInstinct, Elastic, SentinelOne, …):
     <https://github.com/yaronelh/False-Positive-Center> collects each
     vendor's submission portal
   * VirusTotal: share the file and request reanalysis — detections for
     legitimate unsigned tools usually drop as the file gains reputation.
4. **Use the onedir variant** instead: `god2iso-windows-onedir-1.0.0.zip`
   (folder-based, same code) triggers far fewer heuristics than the
   self-extracting onefile build — a widely recommended workaround.
5. **Code signing** (a paid certificate) is the only complete fix — it
   removes the "unsigned + rare" factors for both SmartScreen and AVs.  Not
   done here to keep the tool free and rebuildable.

No tool can guarantee how third-party AV engines will score a fresh unsigned
binary; the honest answer is: verify the hash, rebuild from source, or run
the onedir build, and submit false-positive reports if you want the flags
cleared.

```
# From source (Linux / macOS / Windows with Python 3.9+)
god2iso.bat        # Windows
./god2iso.sh       # Linux / macOS
python3 god2iso.py # anywhere
```

---

## Quick start

Run the tool with no arguments for the interactive wizard, or use the CLI:

```
$ python3 god2iso.py
god2iso.py v1.0.0 - Xbox 360 GOD -> ISO (offline, non-destructive)
Path to the .live file or GOD folder (Enter to quit): C:\Games\45410806
Output ISO path [./45410806.iso]:
Convert now? [Y/n]: y
```

```
$ python3 god2iso.py convert Content/0000000000000000/45410806/00007000/45410806.live
[god2iso] 45410806.live  title 45410806  media 580A3039  2 part file(s)
[god2iso] wrote 45410806.iso (7319068672 bytes)
[god2iso] verify: 512 file(s) in 8 director(ies); default.xex: FOUND
```

Commands:

| command | purpose |
|---|---|
| `god2iso.py` / `--wizard` | interactive, beginner-friendly conversion wizard |
| `god2iso.py info <path>` | show `.live` metadata (Title/Media ID, part count, MHT root hash, …) |
| `god2iso.py convert <path> [-o out.iso] [--force] [--trim] [--fix] [--progress] [--sha256] [--quiet]` | GOD → ISO |
| `god2iso.py list <image.iso>` | list the files inside an Xbox ISO |
| `god2iso.py extract <image.iso> <outdir> [--force]` | extract the ISO's files |
| `god2iso.py rebuild <image.iso> [-o new.iso] [--force]` | rebuild a clean XISO |
| `god2iso.py audit` | prove the tool is fully offline and compiles clean |

`<path>` may be the `.live` file, the `00007000` folder, the `<TitleID>`
folder, or any folder containing a GOD package — the tool finds the parts
automatically in all common layouts:

* `<TitleID>/00007000/<MediaID>.live` + `<MediaID>.live.data/Data0000…`
  (standard iso2god / GOD2ISO layout), or
* `<TitleID>/00007000/<MediaID>` + `<MediaID>.data/Data0000…`
  (**classic iso2god / on-console layout** — the header has no `.live`
  extension; it is auto-detected by the sibling `.data` folder / magic
  bytes), or
* `…/00007000/00000001, 00000002, …` (scene-rip layout), or
* flat `Data####` / `########` files next to the header.

So a game dumped as `Content\0000000000000000\<TitleID>\00007000\`
containing e.g. `082DACEE274BCE0F6ED4` (44 KB) + `082DACEE274BCE0F6ED4.data`
converts fine — even if the folder is mislabeled "XBLA" (GOD is identified
by the `00007000` folder / `.data` parts structure; XBLA would live under
`000D000` as a single STFS file and is a different format).

---

## Safety, security, privacy, non-destructive guarantees

### Non-destructive by construction

* **Inputs are opened read-only.** The GOD package is never modified — this
  is verified by tests that hash the entire source tree before and after a
  conversion.
* **Atomic output.** The ISO is assembled in a hidden temp file in the
  destination directory, `fsync`ed, then atomically renamed into place
  (`os.replace`). A crash, full disk, or any error can never leave a
  half-written `.iso` at the destination — and temp files are always cleaned
  up (tested).
* **No overwrites without consent.** An existing output file is never
  touched unless you pass `--force` (tested: the old file is byte-identical
  after a refused run).
* **Inputs can't be clobbered.** The tool refuses (without `--force`) to use
  an output path that collides with the `.live` file or any part file.
* **Extraction** never deletes anything, never overwrites existing files
  without `--force`, and with `--force` still only touches files that are
  actually in the ISO.
* **Deterministic output**: converting the same GOD twice yields
  byte-identical ISOs (tested).

### Security model

The `.live` file, the part files and the ISO directory tables are treated as
**untrusted input**:

* Every metadata field is bounds-checked; truncated/corrupt input produces a
  clean error, never a crash or a bogus file.
* Path traversal is impossible: ISO table names containing `..`, `/`, `\`,
  NUL or control characters are rejected, and every extraction destination is
  validated to stay inside the output directory (tested with crafted
  malicious images).
* Symlinks in the extraction tree are refused — no symlink-escape writes
  (tested).
* Case-insensitive filename collisions (a hazard on Windows/macOS) are
  detected and refused (tested).
* Windows reserved device names (`CON`, `NUL`, …) and trailing space/dot
  names are rejected (tested).
* No code from the input files is ever executed — there is no scripting,
  no `eval`, no plugin loading.
* Truncated part files and retail-encrypted data are detected and reported
  clearly instead of producing silently broken output.

### Privacy — fully offline

* **The tool never touches the network.** No telemetry, no analytics, no
  title-lookup services (unlike some other tools, which query online APIs),
  no update checks, no external anything.
* This is not just a promise — it's machine-checked: `god2iso.py audit`
  parses every module with `ast` and fails if any network-capable import
  (`socket`, `urllib`, `requests`, `http`, …) exists, then compiles all
  modules. A test asserts the audit stays clean.
* Nothing is logged anywhere; no user data is collected or stored. The only
  files written are the ones you asked for.

### Safety against content you shouldn't process

* Retail-encrypted GOD chunks are **detected** (the de-interleaved stream
  fails to show an XDVDFS volume descriptor) and a clear warning is printed;
  the tool deliberately contains **no decryption code and no key material**.
* Like the reference tool, the output is intended for emulators, PC tools
  (Xbox Image Browser, extract-xiso) and further modding — not for pressing
  retail-grade discs (the per-disc DMI/PFI/SS security sectors are mastering
  secrets that cannot be generated).
* Use it only on content you own or are licensed to process.

### Platform notes

* **Windows**: the exe (above) or `god2iso.bat` from source — works from
  Explorer (double-click → wizard) and from cmd/PowerShell. No admin rights
  needed; multi-gigabyte ISOs work (64-bit file offsets natively); FAT32
  drives work as long as the output volume has space.
* **Linux/macOS**: `./god2iso.sh`, or `python3 god2iso.py` directly.
* **macOS**: same launcher; case-collision protection is on by default.
* Only the Python 3 standard library is used — nothing to install, nothing
  to compile, no DLLs, no containers.

### Resource & robustness guarantees

* **Memory-bounded**: the converter streams part files in fixed-size chunks;
  `extract` and `rebuild` stream file data too (a multi-GB file never gets
  loaded into RAM).  Verified with a 24 MB file and 300+ MB payloads.
* **Bounded parsing**: directory tables larger than 64 MB, trees deeper than
  4 096 entry-pointers, directory nesting beyond 128 levels, or sector-offset
  fixes recursing beyond 64 levels are all rejected as corrupt — a crafted
  image cannot exhaust memory or crash the process.  All produce clean
  "corrupt image" errors, verified on the packaged exe.
* **Console-safe**: filenames outside the console's codepage print as `?`
  instead of crashing the listing (verified: a latin-1 name lists fine).
* **No stray writes**: `info`, `list`, `audit`, `--version`, `--help` write
  nothing anywhere; only the requested output file is ever created (verified
  in a clean directory).

---

## What the converter actually does

1. **Parse the `.live`** (XContent/STFS package, magic `LIVE`): Title ID,
   Media ID, content type, part count, block counts, MHT root hash, and the
   GOD2ISO sector-offset-fix flags.
2. **Locate the parts** (`<live>.data\Data####` or `00007000\00000001` style).
3. **De-interleave each part**: skip the `0x2000`-byte MHT header (master
   hash list + first sub-hash-list), then copy `0xCC000` bytes of game data
   and skip the `0x1000`-byte Merkle hash block, repeating.  The hash blocks
   (SHA-1 of each 204-block group) are the console's streaming-integrity
   tree and are *not* part of the disc image.
4. **Synthesize the 32-sector XSF header** (magic `XSF\x1A`; size fields
   patched from the final length) when the package doesn't embed one — this
   replaces the region a retail disc reserves for its security sectors.
5. **Apply the `.live`-driven sector-offset fix** when the package flags it
   (byte `0x391` bit 0x40, value at `0x395` — ported from GOD2ISO).
6. **Verify**: parse the result as XDVDFS, count files, confirm
   `default.xex`, and warn if the stream looks encrypted.

Optional: `--trim` cuts trailing zero padding to the size declared in the
`.live`; `--fix` applies GOD2ISO's "FixCreateIsoGoodHeader" pass;
`--sha256` prints the output checksum; `--no-verify` skips the deep
verification (see below).

### Precision: byte-exact reconstruction, *proven*

Since v1.3.0 the converter performs a **deep Merkle-hash (MHT)
verification** by default.  Every GOD part stores a SHA-1 hash tree (each
0x1000 data block is hashed into sub hash lists, the sub lists into a
master list per part, chained across parts, and rooted in the `.live`
header).  After extraction the tool recomputes the whole chain:

```
data blocks -> sub hash lists -> master hash lists
            -> cross-part chain -> .live root hash
```

If every hash matches, the extracted ISO is **byte-for-byte identical to
the data that was stored in the GOD** (SHA-1 makes a false pass
practically impossible) - this is independent of the filesystem-level
verification (file counts / default.xex).  A mismatch exits with code 4
and reports which part failed; `--no-verify` disables the check.  The GUI
has a "Deep verify (MHT)" checkbox (on by default).

**What "100% correct" means (and its honest limit):** a GOD package does
not contain the parts of the original retail disc that live outside the
game partition - the security sectors (DMI/PFI/SS), the video partition
and the layer padding are not stored in it, so no tool (including GOD2ISO
or the original iso2god reverse) can rebuild a byte-identical *full
Redump image* from GOD alone.  What the GOD *does* contain - the complete
game partition - is recovered and now **proven** byte-exact by the MHT
verification, which is exactly what emulators (Xenia) and modded consoles
need.

---

## The formats (verified from primary sources)

### GOD part files

Each `Data####` part (as produced by iso2god-rs / classic iso2god):

```
[0x1000  master MHT hash list ]  SHA-1 of each sub-list below
[0x1000  sub-hash-list #0      ]  SHA-1 of each 0x1000 block of data #0
[0xCC000 data block #0         ]  204 x 0x1000
[0x1000  sub-hash-list #1      ]
[0xCC000 data block #1         ]
...                              (repeats; 203 sub-parts in iso2god-rs)
```

* Constants (iso2god-rs `god/mod.rs`): `BLOCK_SIZE=0x1000`,
  `BLOCKS_PER_SUBPART=0xCC` (204), `SUBPARTS_PER_PART=0xCB` (203),
  `BLOCKS_PER_PART=0xA1C4` ≈ 162 MB of data per part.
* Classic iso2god split parts at the FAT32 4 GB limit instead; the converter
  is agnostic to part size and count.
* The MHT is chained across parts; its root digest and the block counts are
  stored in the `.live` (offsets `0x37D`, `0x392`).
* Part 0's payload may start with an embedded **XSF header** (classic
  iso2god) or directly at the ISO's LBA 32 (iso2god-rs) — both handled.

### The `.live` file

A binary **XContent/STFS** package (magic `LIVE` @0x000), not XML:

| offset | field |
|---|---|
| 0x000 | magic `"LIVE"` / `"CON "` / `"PIRS"` |
| 0x344 | content type (0x7000 = Games on Demand, 0x5000 = Xbox Original) |
| 0x354 | **Media ID** |
| 0x360 | **Title ID** |
| 0x364 | platform / executable type / disc number / disc count |
| 0x37D | MHT root hash (20 bytes) |
| 0x391 | flags byte — bit 0x40: sector-offset fix present |
| 0x392 | blocks allocated (u24 BE) |
| 0x395 | u32 LE: sector-offset value used by the fix (`2*val − 34`) |
| 0x39D/0x3A0 | part ("Data file") count (builders differ; all read) |
| 0x3A1/0x3A4 | combined data size (builders differ; all read) |

### The XDVDFS ("XISO") disc image

* 2048-byte sectors, little-endian.
* ISO9660 PVD at 0x8000 (for burn-tool auto-detection).
* Volume descriptor at **LBA 32 (0x10000)**: `MICROSOFT*XBOX*MEDIA` magic,
  u32 root table sector @0x14, u32 root table size @0x18, FILETIME @0x1C,
  trailing magic @0x7EC.
* Directory entries: `u16 left, u16 right (dword offsets), u32 sector,
  u32 size, u8 attributes, u8 name-length, name`, padded to 4 with 0xFF;
  0xFFFF = pad/end; tables padded to sector boundaries.
* Files sector-aligned, padded with 0xFF.

### Corrections to common misconceptions

| Common claim | Reality |
|---|---|
| "GOD chunks are raw ISO sectors; just concatenate them" | ❌ Parts interleave SHA-1 Merkle hash blocks (0x1000 per 0xCC000) and start with a 0x2000 MHT header. Naive concatenation corrupts the image every ~0.8 MB. |
| "The .live file is plaintext XML" | ❌ Binary XContent/STFS package (magic `LIVE`) with title/media IDs, block counts and the MHT root hash. |
| "No encryption on the container; chunks are unencrypted dumps" | ❌ **Retail** GOD chunks are AES-128-CBC encrypted per title key. Only modded/decrypted content (iso2god output, RGH dumps) is plaintext. |
| "Prepend the XISO volume descriptor (magic …)" | ❌ The descriptor lives at LBA 32 (0x10000) of the image; sectors 0–31 are the security/XSF region. Nothing is "prepended at byte 0". |
| "XGD2 ≈ 7.3 GB, XGD3 ≈ 8.1 GB" | Commonly cited usable capacities: XGD2 ≈ 7.3 GB, XGD3 ≈ 8.7 GB. |
| "Inject PFI/DMI/SS sectors to make it bootable" | ❌ Those are per-disc mastering secrets (that is the DRM); they cannot be generated. Emulator/PC tooling doesn't need them; the XSF header replaces the region. |
| "Needs a title key / security key" | Only for retail-encrypted content. This toolchain deliberately contains no key material and no decryption code. |
| "GOD parts are always ~4 GB" | Classic iso2god: yes (FAT32 limit). iso2god-rs: 0xA1C4 blocks ≈ 162 MB per part. The converter handles both. |

---

## Testing

```
python3 tests/test_roundtrip.py       # 14 tests - byte-exact format round trips
python3 tests/test_safety.py          # 32 tests - safety/security/privacy
python3 tests/test_gui.py             # 5 tests  - GUI helpers + headless fallback
EXTRACT_XISO=path/to/extract-xiso python3 tests/test_cross_tool.py   # 5 tests
xvfb-run -a python3 tests/gui_e2e.py  # full GUI: convert+extract+rebuild clicks
```

The GUI is verified end-to-end on a virtual display (xvfb): the window
builds with its three tabs, and driving the actual Convert/Extract/Rebuild
buttons through the threaded pipeline produces valid output.  The packaged
exe's GUI is additionally launched under Wine + xvfb and confirmed to render
(controls visible in a screenshot).

The **cross-tool suite** builds the actual reference implementation
(extract-xiso v2.7.1, XboxDev) from source and uses it as an independent
oracle:

* an ISO built by this project's writer must extract with extract-xiso to
  byte-identical files (including directories with >204 entries, which force
  real tree offsets);
* an ISO built by extract-xiso (including empty directories) must parse and
  extract byte-identically with this project's reader;
* the complete GOD → ISO pipeline (both XSF flavors, multi-part) must produce
  an ISO that extract-xiso extracts back to the original files;
* extract-xiso's own rewrite (`-r`) output must read back cleanly.

The round-trip suite generates synthetic but format-faithful GOD packages
(real `.live` metadata with MHT root hash and block counts, realistic
master/sub hash-list part layout, both XSF flavors, period-aligned multi-part
splits, padding, trim, the `00007000\00000001` layout, inflated-sector offset
fixing) and verifies conversions **byte-for-byte**, plus:

* source tree untouched after conversion (full-tree hashing);
* existing outputs protected without `--force`, atomic cleanup on failure,
  deterministic output;
* malicious ISO names (`../evil`, backslashes, NUL, control chars, `CON`),
  symlink escapes and case collisions refused;
* static no-network audit stays clean; `audit` command returns 0;
* encrypted-data detection; truncated-part detection;
* every error path produces a clean message with no traceback;
* wizard works with piped input; `--progress`, `--sha256` behave.

> No real retail GOD image was available in this environment; the
> implementation is a behavioral mirror of the open-source reference tools
> and is validated by the synthetic round trips and the extract-xiso
> cross-checks. Sanity-check any real conversion with `list`/`extract` and a
> tool such as Xbox Image Browser or extract-xiso before relying on it.

## References

* GOD2ISO (raburton) — reference converter: <https://github.com/raburton/god2iso>
* iso2god-rs (iliazeus) — modern producer (part layout, MHT, CON header):
  <https://github.com/iliazeus/iso2god-rs>
* Iso2God (r4dius) — classic iso2god decompilation:
  <https://github.com/r4dius/Iso2God>
* extract-xiso (XboxDev) — XDVDFS reference implementation:
  <https://github.com/XboxDev/extract-xiso>
* ConsoleMods wiki — GOD2ISO / ISO2GOD guides:
  <https://consolemods.org/wiki/Xbox_360:GOD2ISO>, <https://consolemods.org/wiki/Xbox_360:ISO2GOD>
* free60 wiki — STFS/XContent metadata: <https://free60.org/System-Software/Formats/STFS/>
* xboxdevwiki — XDVDFS: <https://xboxdevwiki.net/XDVDFS>

## License

MIT (see `LICENSE`). Format documentation derived from the listed open
sources; no code was copied from them — their published layouts were used as
behavioral specifications.
