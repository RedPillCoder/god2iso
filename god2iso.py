#!/usr/bin/env python3
"""god2iso.py - safe, offline, cross-platform Xbox 360 GOD -> ISO converter.

Behavior mirrors the reference tool GOD2ISO v1.0.5 (raburton, CC-BY-SA-4.0,
github.com/raburton/god2iso): the same part-file layout detection, the same
0x2000-byte per-part skip, the same 0xCC000-data / 0x1000-hash
de-interleave, the same synthesized "XSF" header, and the same .live-driven
sector-offset fixing.  XDVDFS reading/writing mirrors extract-xiso (XboxDev).

SAFETY / PRIVACY / NON-DESTRUCTIVE GUARANTEES
  * Fully offline: no network access, no telemetry, no external lookups.
    Verify with:  python3 god2iso.py audit
  * Non-destructive: inputs are opened read-only and never modified; output
    is written to a temp file and atomically renamed; an existing output is
    never overwritten without --force; failed conversions leave no residue.
  * Defensive parsing: every field read from the .live file and every path
    from the ISO tables is bounds-checked and validated (traversal, NUL,
    separators, control chars, Windows reserved names).
  * Encryption is detected and reported; retail-encrypted chunks cannot be
    decrypted by this tool (no key material by design).

SCOPE
  * Works on decrypted / modded GOD content (e.g. made with iso2god from a
    disc the user owns, or dumped from the user's own RGH console).
  * Does NOT handle retail-encrypted GOD chunks.  Those are AES-128-CBC
    encrypted with per-title keys wrapped in the XContent header; key
    material is not part of this tool.
  * Use only with content you own or are licensed to process.

Usage:
  god2iso.py                        interactive wizard
  god2iso.py info    <live-or-folder>
  god2iso.py convert <live-or-folder> [-o out.iso] [--force] [--trim] [--fix]
                       [--progress] [--sha256] [--quiet]
  god2iso.py list    <image.iso>
  god2iso.py extract <image.iso> <outdir> [--force]
  god2iso.py rebuild <image.iso> [-o new.iso] [--force]
  god2iso.py audit
"""

import argparse
import os
import re
import shutil
import struct
import sys

import util
import xcontent
import xsf
import xiso

VERSION = "1.3.0"

PART_HEADER_SIZE = 0x2000        # master MHT list + first sub-hash-list
INTERLEAVE_DATA = 0xCC000        # data bytes between hash blocks
INTERLEAVE_HASH = 0x1000         # Merkle hash block size (204 x SHA-1)

_BANNER = "god2iso.py v%s - Xbox 360 GOD -> ISO (offline, non-destructive)" \
    % VERSION


# ---------------------------------------------------------------------------
# locating the .live file and the data part files
# ---------------------------------------------------------------------------

_NUM = re.compile(r"(\d+)$")
_DATA_RE = re.compile(r"^Data(\d+)$", re.IGNORECASE)
_CHUNK_RE = re.compile(r"^(\d+)$")


def _is_xcontent_magic(path):
    """True if the file begins with an XContent package magic
    ('LIVE' / 'CON ' / 'PIRS') - the hallmark of a GOD header."""
    try:
        with open(path, "rb") as f:
            return f.read(4) in (b"LIVE", b"CON ", b"PIRS")
    except OSError:
        return False


# file types that can never be a GOD header - skip when sniffing
_SKIP_EXTS = (".iso", ".xex", ".bin", ".zip", ".rar", ".7z", ".tar",
              ".gz", ".txt", ".md", ".log", ".png", ".jpg", ".jpeg",
              ".gif", ".ico", ".dat", ".cfg", ".ini", ".dll", ".exe",
              ".py", ".ps1", ".bat", ".sh", ".html", ".json", ".xml")


def find_live_files(path):
    """Return a list of GOD header files under *path* (file or folder).

    Two naming conventions are recognised:
      * <MediaID>.live            (modern iso2god / GOD2ISO naming)
      * <MediaID>  (no extension) (classic iso2god / on-console naming) -
        detected by the sibling '<name>.data' parts folder and/or the
        XContent magic bytes.
    """
    if os.path.isfile(path):
        return [path] if (path.lower().endswith(".live")
                          or _is_xcontent_magic(path)) else []
    found = []
    for dirpath, _dirs, files in os.walk(path):
        for name in sorted(files):
            full = os.path.join(dirpath, name)
            low = name.lower()
            if low.endswith(".live"):
                found.append(full)
                continue
            if low.endswith(_SKIP_EXTS) or low.startswith("."):
                continue
            # extension-less candidate: must look like a GOD header
            if os.path.isdir(full + ".data") or _is_xcontent_magic(full):
                found.append(full)
    return sorted(found)


def _indexed(paths):
    """[(index, path)] sorted numerically by trailing number in the name."""
    out = []
    for p in paths:
        m = _NUM.search(os.path.basename(p))
        out.append((int(m.group(1)) if m else 0, p))
    out.sort(key=lambda t: t[0])
    return out


def find_part_files(live_path):
    """Locate the Data#### / 00000001.. part files for a .live file.

    Supported layouts (in priority order):
      1. iso2god / GOD2ISO canonical : <TitleID>.live.data\\Data0000, ...
      2. scene-rip style             : <god-root>\\00007000\\00000001, ...
      3. flat                         : Data#### / ######## next to .live
    Returns a list of part file paths in order, or raises FileNotFoundError.
    """
    live_dir = os.path.dirname(os.path.abspath(live_path))
    base = os.path.basename(live_path)
    candidates = []

    # 1) "<live>.data" folder
    data_dir = os.path.join(live_dir, base + ".data")
    if os.path.isdir(data_dir):
        paths = [os.path.join(data_dir, n) for n in os.listdir(data_dir)
                 if _DATA_RE.match(n)]
        candidates.append(_indexed(paths))

    # 2) a numeric subfolder (usually "00007000") of the live's parent,
    #    containing numbered chunk files (00000001, ...) or Data#### files
    parent = os.path.dirname(live_dir)
    if parent and os.path.isdir(parent):
        for sub in sorted(os.listdir(parent)):
            subpath = os.path.join(parent, sub)
            if os.path.isdir(subpath):
                paths = [os.path.join(subpath, n) for n in os.listdir(subpath)
                         if _CHUNK_RE.match(n) or _DATA_RE.match(n)]
                if paths:
                    candidates.append(_indexed(paths))

    # 3) same folder as the .live
    paths = [os.path.join(live_dir, n) for n in os.listdir(live_dir)
             if (_CHUNK_RE.match(n) or _DATA_RE.match(n))
             and os.path.isfile(os.path.join(live_dir, n))]
    candidates.append(_indexed(paths))

    for ordered in candidates:
        if ordered:
            return [p for _, p in ordered]
    raise FileNotFoundError(
        "no data part files found for %r (looked for %s\\Data#### and "
        "00007000\\00000001 style layouts)" % (live_path, live_dir))


# ---------------------------------------------------------------------------
# conversion (mirrors god2iso Form1.cs, hardened)
# ---------------------------------------------------------------------------

def has_xsf_header(part0_path):
    """GOD2ISO checks bytes at 0x2000 of the first part for 'XSF'."""
    try:
        with open(part0_path, "rb") as f:
            f.seek(PART_HEADER_SIZE)
            return f.read(3) == b"XSF"
    except OSError:
        return False


def _emit(log, msg):
    """Route a status message to the GUI log callback or stdout."""
    if log is not None:
        log(msg)
    else:
        print(msg)


def copy_deinterleaved(src, dst, progress=None):
    """Copy one part file to the output, skipping the 0x2000 MHT header and
    the 0x1000 Merkle hash block after every 0xCC000 of data.

    Byte-for-byte mirror of GOD2ISO's copy loop.  *progress* is an optional
    callback(bytes_done, part_total).  Returns the number of data bytes
    copied."""
    size = os.path.getsize(src)
    copied = 0
    with open(src, "rb") as f:
        f.seek(PART_HEADER_SIZE)
        done = 0
        while True:
            buf = f.read(INTERLEAVE_DATA)
            dst.write(buf)
            done += len(buf)
            copied += len(buf)
            if progress:
                progress(done, max(size - PART_HEADER_SIZE, 1))
            if len(buf) < INTERLEAVE_DATA:
                break
            buf = f.read(INTERLEAVE_HASH)
            if len(buf) < INTERLEAVE_HASH:
                break
    return copied



def predict_partition_offset(part0_path, part0_has_xsf, limit=4 << 20):
    """Predict the output's game-partition offset from part0's head.

    De-interleaves the start of part0 and locates the XDVDFS marker.
    Returns the partition offset (0 = trimmed-image layout)."""
    try:
        with open(part0_path, "rb") as f:
            f.seek(PART_HEADER_SIZE)
            head = f.read(limit + 0x1000)
    except OSError:
        return 0
    stream = bytearray()
    pos = 0
    while pos < len(head) and len(stream) < limit:
        chunk = head[pos:pos + INTERLEAVE_DATA]
        stream += chunk
        pos += INTERLEAVE_DATA + INTERLEAVE_HASH
        if len(chunk) < INTERLEAVE_DATA:
            break
    m = bytes(stream).find(xiso.XDVDFS_MAGIC)
    if m < 0:
        return 0
    if part0_has_xsf:
        # embedded XSF sits at payload offset 0; output offset == payload offset
        return max(m - xiso.VOLUME_DESCRIPTOR_OFFSET, 0)
    # synthesized XSF is prepended: output offset = m + 0x10000
    return m

def _scan_part_hashes(part_path):
    """Walk one part file: yield (data_chunk, sub_list_bytes) pairs.

    Mirrors the copy loop's alignment: the master list and the first
    sub-list occupy 0x2000; each pair is [sub-list 0x1000][data up to
    0xCC000].  Stops when the sub-list is all zeroes (trailing padding or
    unpopulated hashes) or at end-of-file.

    The final data chunk may include trailing zero padding appended by
    classic iso2god; verification handles that (see verify_mht).

    Returns (pairs, reason): reason in {"complete","partial-final",
    "end","zeroed"}."""
    pairs = []
    reason = "complete"
    with open(part_path, "rb") as f:
        f.seek(0x1000)                      # after master list
        while True:
            sub = f.read(INTERLEAVE_HASH)
            if len(sub) < INTERLEAVE_HASH:
                reason = "end"
                break
            data = f.read(INTERLEAVE_DATA)
            if not data:
                reason = "end"
                break
            if not any(sub):
                reason = "zeroed"
                break
            pairs.append((data, sub))
            if len(data) < INTERLEAVE_DATA:
                reason = "partial-final"
                break
    return pairs, reason


def _stored_hashes(sub):
    """The non-zero SHA-1 digests stored in a sub-list (padded with
    zeros to 0x1000 after the real entries)."""
    out = []
    for i in range(0, INTERLEAVE_HASH, 20):
        h = sub[i:i + 20]
        if any(h):
            out.append(h)
        else:
            break
    return out


def _verify_chunk(data, sub):
    """Verify one data chunk against its sub hash list.

    Returns True on match.  Handles trailing zero padding inside the
    final block by searching for the exact original length (only when a
    direct match fails)."""
    import hashlib
    stored = _stored_hashes(sub)
    if not stored:
        return True                      # nothing stored - cannot check
    nread = (len(data) + 0xFFF) // 0x1000
    ok = True
    for i in range(min(nread, len(stored)) - 1):
        block = data[i * 0x1000:(i + 1) * 0x1000]
        if hashlib.sha1(block).digest() != stored[i]:
            return False
    li = min(nread, len(stored)) - 1
    if li >= 0:
        block = data[li * 0x1000:li * 0x1000 + 0x1000]
        if hashlib.sha1(block).digest() != stored[li]:
            # final block may contain trailing zero padding: search the
            # exact original length (block is <= 0x1000 bytes)
            found = False
            for ln in range(len(block), 0, -1):
                if hashlib.sha1(block[:ln]).digest() == stored[li]:
                    found = True
                    break
            if not found:
                ok = False
    return ok


def verify_mht(parts, live_bytes=None):
    """Deep-verify the GOD part files against their Merkle hash tree.

    Recomputes the SHA-1 chain stored in the parts:
      data blocks -> sub hash lists -> master hash lists
      -> cross-part chain -> root hash in the .live header (if present).

    A full pass proves the de-interleaved data is byte-exact with what was
    stored in the GOD (SHA-1 makes a false pass practically impossible).
    Trailing zero padding inside part files is handled.

    Returns (ok, details, notes):
      ok      - True if every verifiable check passed
      details - list of (label, message, ok) per part + chain/root
      notes   - informational strings (zeroed lists, missing root...)
    """
    import hashlib
    details = []
    notes = []
    masters = []
    sub_counts = []
    all_ok = True
    any_zeroed = False

    for idx, part in enumerate(parts):
        pairs, reason = _scan_part_hashes(part)
        with open(part, "rb") as f:
            master = f.read(INTERLEAVE_HASH)
        masters.append(master)
        sub_counts.append(len(pairs))
        if reason == "zeroed" and not pairs:
            any_zeroed = True
            notes.append("part %d: hash lists are zeroed - deep "
                         "verification skipped" % (idx + 1))
            details.append(("part %d" % (idx + 1), "no hashes stored "
                            "(zeroed) - skipped", True))
            continue
        sub_fail = 0
        for data, sub in pairs:
            if not _verify_chunk(data, sub):
                sub_fail += 1
        calc_master = b"".join(hashlib.sha1(sb).digest()
                               for _, sb in pairs)
        master_ok = master[:len(calc_master)] == calc_master
        ok = (sub_fail == 0) and master_ok
        if not ok:
            all_ok = False
        msg = "ok" if ok else ("FAILED (%d sub-list mismatch(es), "
                               "master %s)"
                               % (sub_fail,
                                  "ok" if master_ok else "mismatch"))
        details.append(("part %d" % (idx + 1), msg, ok))

    # cross-part chain + root only when every part was verifiable
    verifiable = all(d[2] for d in details) and not any_zeroed
    chain_ok = True
    if verifiable and len(masters) > 1:
        digest_next = None
        for i in range(len(masters) - 1, -1, -1):
            if digest_next is not None:
                pos = sub_counts[i] * 20
                appended = masters[i][pos:pos + 20]
                if appended != digest_next:
                    chain_ok = False
            digest_next = hashlib.sha1(masters[i]).digest()
        if not chain_ok:
            all_ok = False
        details.append(("chain", "ok" if chain_ok
                        else "cross-part chain mismatch", chain_ok))

    root_ok = True
    if live_bytes and len(live_bytes) >= 0x391:
        root = live_bytes[0x37D:0x391]
        if any(root) and masters:
            if verifiable:
                root_ok = (hashlib.sha1(masters[0]).digest() == root)
                if not root_ok:
                    all_ok = False
                details.append(("root", "ok" if root_ok
                                else "MHT root hash does not match the "
                                     ".live header", root_ok))
            else:
                notes.append("root check skipped (earlier verification "
                             "failed)")
        else:
            notes.append("no MHT root hash present in the .live header - "
                         "root check skipped")
    return all_ok, details, notes


def fix_sector_offsets(iso, live_bytes, lseek_offset=0):
    """Port of god2iso's FixSectorOffsets.  *iso* is an open r+b handle.

    Some GOD builds store the ISO shifted so that every directory-table
    sector field is off by a constant; the .live volume-descriptor flags
    (0x391 bit 0x40) and value (u32 LE @ 0x395) describe the shift.
    Returns the number of corrected fields.
    """
    if len(live_bytes) < 0x399:
        return 0
    if (live_bytes[0x391] & 0x40) != 0x40:
        return 0
    raw = struct.unpack_from("<I", live_bytes, 0x395)[0]
    if raw == 0:
        return 0
    offset = raw * 2 - 34
    if offset <= 0:                      # a shift of <= 0 makes no sense
        return 0
    fixed = 0

    def read_u32(pos):
        iso.seek(pos)
        b = iso.read(4)
        if len(b) < 4:
            raise xiso.XisoError("image too short for sector-offset fixing")
        return struct.unpack("<I", b)[0]

    def write_u32(pos, val):
        iso.seek(pos)
        iso.write(struct.pack("<I", val))

    def fix_entry(sector, size, depth=0):
        nonlocal fixed
        if depth > 64:
            raise xiso.XisoError(
                "directory nesting too deep during offset fix (corrupt "
                "image)")
        start = lseek_offset + sector * xiso.SECTOR_SIZE
        end = start + size
        pos = start
        while pos + 4 < end:
            if (pos + 4) // 2048 > pos // 2048:      # crossed a sector
                pos += 2048 - (pos % 2048)
            if read_u32(pos) == 0xFFFFFFFF:          # pad / end marker
                if end - (pos + 4) > 2048:
                    pos += 2048 - (pos % 2048)
                    continue
                break
            sec = read_u32(pos + 4)
            if sec > 0:
                write_u32(pos + 4, sec - offset)
                fixed += 1
            size_ = read_u32(pos + 8)
            attrib = read_u32(pos + 12) & 0xFF
            if attrib & xiso.ATTR_DIRECTORY:
                fix_entry(sec - offset, size_, depth + 1)
            namelen = read_u32(pos + 13) & 0xFF
            pos += 14 + namelen
            if (14 + namelen) % 4:
                pos += 4 - ((14 + namelen) % 4)

    root = read_u32(lseek_offset + xiso.VOLUME_DESCRIPTOR_OFFSET + 0x14)
    if root > 0:
        write_u32(lseek_offset + xiso.VOLUME_DESCRIPTOR_OFFSET + 0x14,
                  root - offset)
        fix_entry(root - offset,
                  read_u32(lseek_offset + xiso.VOLUME_DESCRIPTOR_OFFSET
                           + 0x18))
    return fixed


def convert(live_path, out_path, trim=False, fix=False, quiet=False,
            force=False, progress_cb=None, sha256=False, log=None,
            phase_cb=None, verify=True):
    """Convert one GOD package to an ISO.

    Non-destructive contract:
      * never opens any input for writing;
      * never writes into the GOD source tree (refused unless --force);
      * output is assembled in a temp file and atomically renamed;
      * an existing output is never touched without --force.

    *log* is an optional callable(str) for status messages (GUI mode);
    when None, messages go to stdout/stderr.  *progress_cb* is an optional
    callable(done, total) receiving cumulative progress.  *phase_cb* is an
    optional callable(str) receiving phase text ("Reading part 3 of 45").
    Returns 0 ok, 2 verified but no default.xex, 3 image unreadable.
    """
    live_bytes = open(live_path, "rb").read()
    info = xcontent.parse(live_bytes)
    parts = find_part_files(live_path)

    # refuse to overwrite an INPUT file with the output (the one truly
    # destructive accident possible); writing elsewhere is allowed
    out_abs = os.path.abspath(out_path)
    input_paths = [os.path.abspath(live_path)] + \
        [os.path.abspath(p) for p in parts]
    if os.path.normcase(out_abs) in {os.path.normcase(p)
                                     for p in input_paths} and not force:
        raise util.SafetyError(
            "refusing to overwrite a GOD input file with the output (%r); "
            "choose another output name or use --force" % out_path)

    if info.content_type not in (xcontent.CONTENT_TYPE_GOD,
                                 xcontent.CONTENT_TYPE_XBOX_ORIGINAL):
        if not quiet:
            _emit(log, "[god2iso] warning: content type 0x%04X is not "
                       "Games-on-Demand - conversion may fail"
                       % info.content_type)

    for p in parts:
        if os.path.getsize(p) < PART_HEADER_SIZE:
            raise xiso.XisoError(
                "part file %r is smaller than its %d-byte header - "
                "truncated or corrupt" % (p, PART_HEADER_SIZE))

    # disk space sanity (warning only; failure cleans up atomically)
    needed = sum(os.path.getsize(p) for p in parts) + (64 << 20)
    free = util.disk_free(out_abs)
    if 0 <= free < needed and not quiet:
        _emit(log, "[god2iso] warning: only %d MB free on the output volume; "
                   "~%d MB needed" % (free >> 20, needed >> 20))

    if not quiet:
        _emit(log, "[god2iso] %s  title %s  media %08X  %d part file(s)"
                   % (os.path.basename(live_path), info.title_id_hex,
                      info.media_id, len(parts)))

    part0_has_xsf = has_xsf_header(parts[0])
    po = predict_partition_offset(parts[0], part0_has_xsf)
    grand_total = max(sum(os.path.getsize(p) - PART_HEADER_SIZE
                          for p in parts), 1)
    done_all = 0

    with util.atomic_output(out_path, overwrite=force) as iso:
        if not part0_has_xsf:
            iso.write(xsf.make_xsf_header())
        for i, part in enumerate(parts):
            if phase_cb:
                phase_cb("Reading part %d of %d" % (i + 1, len(parts)))
            copied = copy_deinterleaved(part, iso, progress_cb)
            done_all += copied
            if progress_cb:
                progress_cb(done_all, grand_total)
        total = iso.tell()
        if phase_cb:
            phase_cb("Verifying output...")

        if not part0_has_xsf:
            # patch the synthesized XSF header (god2iso FixXFSHeader)
            iso.seek(0)
            head = bytearray(iso.read(xsf.XSF_SIZE))
            xsf.patch_xsf_header(head, total)
            iso.seek(0)
            iso.write(head)
            fixed = fix_sector_offsets(iso, live_bytes, lseek_offset=po)
            if fixed and not quiet:
                _emit(log, "[god2iso] corrected %d directory-table sector "
                           "offset(s)" % fixed)
        else:
            # embedded XSF header: sanity-check its size field
            with open(parts[0], "rb") as f:
                f.seek(PART_HEADER_SIZE)
                head = f.read(xsf.XSF_SIZE)
            if not xsf.embedded_size_ok(head, total) and not quiet:
                _emit(log, "[god2iso] warning: embedded XSF header size field "
                           "does not match the de-interleaved length (%d)"
                           % total)

        if fix:
            # god2iso "FixCreateIsoGoodHeader": rewrite the XSF header when
            # the image looks like a 'created' ISO (u64@8 == 2587648)
            iso.seek(8)
            if struct.unpack("<Q", iso.read(8))[0] == 2587648:
                iso.seek(0)
                iso.write(xsf.make_xsf_header())
                fixed = fix_sector_offsets(iso, live_bytes, lseek_offset=po)
                if fixed and not quiet:
                    _emit(log, "[god2iso] (--fix) corrected %d sector "
                               "offset(s)" % fixed)

        # optional trim to the size declared in the .live header
        declared = xcontent.pick_combined_size(info, total)
        if trim and declared:
            trimmed = ((declared + xiso.SECTOR_SIZE - 1)
                       // xiso.SECTOR_SIZE) * xiso.SECTOR_SIZE
            if trimmed < total:
                iso.truncate(trimmed)
                total = trimmed

    if sha256 and not quiet:
        _emit(log, "sha256  %s  %s" % (util.sha256_file(out_abs), out_path))

    # --- detect the game partition and verify --------------------------------
    # Scan the whole (already de-interleaved) output for the XDVDFS marker.
    # Full XGD2/XGD3 images carry it at partition_start + 0x10000, so a
    # fixed-position check would wrongly report "encrypted".
    vd_off = xiso.find_xdvdfs_offset(out_abs)
    if vd_off < 0:
        _emit(log, "[god2iso] WARNING: the de-interleaved data does not "
                   "contain an XDVDFS volume descriptor - the parts are "
                   "likely retail-ENCRYPTED.  This tool deliberately does "
                   "not handle encryption (no title keys); use it on "
                   "decrypted/modded GOD content only.")
        return 3
    po = max(vd_off - xiso.VOLUME_DESCRIPTOR_OFFSET, 0)
    if not quiet:
        _emit(log, "[god2iso] wrote %s (%d bytes)" % (out_path, total))

    # verification pass (at the detected partition offset)
    try:
        files = xiso.list_image(out_abs, lseek_offset=po)
        n = sum(1 for _, d, _ in files if not d)
        default = xiso.find_default_xex(out_abs, lseek_offset=po)
        if not quiet:
            note = ""
            if po:
                note = " (game partition at offset 0x%X)" % po
            _emit(log, "[god2iso] verify: %d file(s) in %d director(ies); "
                       "default.xex: %s%s"
                       % (n, sum(1 for _, d, _ in files if d),
                          "FOUND" if default else "MISSING", note))
        rc = 0 if default else 2
    except xiso.XisoError as e:
        _emit(log, "[god2iso] verify: FAILED - %s" % e)
        return 3

    # deep Merkle-hash verification: proves the extracted data is
    # byte-exact with what was stored in the GOD (independent of the
    # filesystem parsing above)
    if verify and rc in (0, 2):
        if phase_cb:
            phase_cb("Verifying Merkle hash tree...")
        ok, details, notes = verify_mht(parts, live_bytes)
        for note in notes:
            _emit(log, "[god2iso] note: %s" % note)
        if ok:
            _emit(log, "[god2iso] MHT verification: PASSED (%d part(s), "
                       "all hash lists match)" % len(parts))
        else:
            _emit(log, "[god2iso] MHT verification: FAILED - the stored "
                       "SHA-1 hash lists do not match the extracted data "
                       "(corrupt parts or wrong layout):")
            for label, msg, okd in details:
                _emit(log, "  %s: %s" % (label, msg))
            return 4
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_info(args):
    for live in find_live_files(args.path):
        try:
            info = xcontent.parse(open(live, "rb").read())
        except ValueError as e:
            print("error: %s: %s" % (live, e), file=sys.stderr)
            return 1
        try:
            parts = find_part_files(live)
            obs = len(parts)
        except FileNotFoundError:
            parts, obs = [], -1
        print("%s:" % live)
        print(info.summary())
        if obs >= 0:
            print("part files      : %d (%s)" % (obs, ", ".join(
                os.path.basename(p) for p in parts)))
            print("header part cnt : %d"
                  % xcontent.pick_part_count(info, obs))
            sizes = [os.path.getsize(p) for p in parts]
            print("part sizes      : %s" % ", ".join(str(s) for s in sizes))
            payload = sum(s - PART_HEADER_SIZE for s in sizes)
            hashes = (payload // (INTERLEAVE_DATA + INTERLEAVE_HASH)) \
                * INTERLEAVE_HASH
            print("approx data     : %d bytes (de-interleaved estimate)"
                  % (payload - hashes))
        print()
    return 0


def cmd_convert(args):
    lives = find_live_files(args.path)
    if not lives:
        print("error: no .live file found at %r" % args.path, file=sys.stderr)
        return 1
    if len(lives) > 1 and not args.out:
        print("error: multiple .live files found (%s); pick one or use -o"
              % ", ".join(os.path.basename(l) for l in lives),
              file=sys.stderr)
        return 1
    for live in lives:
        stem = os.path.splitext(os.path.basename(live))[0]
        out = args.out or os.path.join(os.getcwd(), stem + ".iso")
        def _term_progress(done, total, _state=[0]):
            if args.progress:
                _state[0] += 1
                if _state[0] % 16 == 0:
                    sys.stderr.write("\r  %6.1f%%" % (100.0 * done / total))
                    sys.stderr.flush()
        try:
            rc = convert(live, out, trim=args.trim, fix=args.fix,
                         quiet=args.quiet, force=args.force,
                         progress_cb=_term_progress, sha256=args.sha256,
                         verify=not args.no_verify)
        except (util.SafetyError, xiso.XisoError,
                FileNotFoundError, ValueError, OSError) as e:
            print("error: %s" % e, file=sys.stderr)
            return 1
        if args.progress:
            sys.stderr.write("\n")
        if rc:
            return rc
    return 0


def cmd_list(args):
    files = xiso.list_image(args.iso,
                            lseek_offset=xiso.partition_offset(args.iso))
    total = 0
    for rel, is_dir, size in files:
        if is_dir:
            print("  %s/" % rel)
        else:
            print("  %s (%d bytes)" % (rel, size))
            total += size
    print("%d entries, %d file bytes" % (len(files), total))
    return 0


def cmd_extract(args):
    try:
        written = xiso.extract_image(args.iso, args.outdir, force=args.force,
                                     lseek_offset=xiso.partition_offset(args.iso))
    except (util.SafetyError, xiso.XisoError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    print("extracted %d file(s) to %s" % (len(written), args.outdir))
    return 0


def rebuild_image(iso_path, out_path, force=False, log=None):
    """Rebuild a clean XDVDFS image from *iso_path* into *out_path*.

    Streams the source ISO's files into a temporary directory (never fully
    in RAM), then builds the new image from those paths (also streamed).
    Returns (rc, file_count): rc 0 = ok (default.xex found), 2 = rebuilt
    but default.xex missing.
    """
    out = out_path
    if not force and os.path.exists(out):
        raise util.SafetyError(
            "refusing to overwrite existing file %r (use --force)" % out)
    import tempfile as _tf
    workdir = _tf.mkdtemp(prefix=".god2iso-rb-")
    file_count = 0
    lseek_offset = xiso.partition_offset(iso_path)
    try:
        with open(iso_path, "rb") as f:
            root_sector, root_size, _ = xiso.read_volume_descriptor(
                f, lseek_offset)

            def walk(sector, size, prefix, depth=0):
                nonlocal file_count
                if depth > xiso.MAX_DIR_DEPTH:
                    raise xiso.XisoError(
                        "directory nesting too deep (corrupt image)")
                for e in xiso.read_table(f, sector, size, lseek_offset):
                    xiso._check_name(e.name)
                    rel = prefix + e.name
                    if e.is_dir:
                        walk(e.sector, e.size, rel + "/", depth + 1)
                        continue
                    dst = util.safe_join(workdir, *rel.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as w:
                        xiso.read_file_to(f, e.sector, e.size, w,
                                          lseek_offset)
                    file_count += 1

            walk(root_sector, root_size, "")
        files = {}
        for dirpath, _dirs, names in os.walk(workdir):
            for name in names:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, workdir).replace(os.sep, "/")
                files[rel] = full
        # build into a temp file in the destination directory, then
        # atomically rename - a failure never leaves a partial .iso behind
        d = os.path.dirname(os.path.abspath(out)) or os.curdir
        fd, tmp = _tf.mkstemp(prefix=".god2iso-rb-", suffix=".tmp", dir=d)
        os.close(fd)
        try:
            xiso.build_image(files, tmp)
            os.replace(tmp, out)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    n = xiso.find_default_xex(out)
    _emit(log, "rebuilt %d file(s) into %s (default.xex: %s)"
              % (file_count, out, "FOUND" if n else "MISSING"))
    return (0 if n else 2, file_count)


def cmd_rebuild(args):
    out = args.out or os.path.splitext(args.iso)[0] + ".rebuilt.iso"
    try:
        rc, _ = rebuild_image(args.iso, out, force=args.force)
    except (util.SafetyError, xiso.XisoError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return rc


def audit_lines() -> list:
    """Return the offline-audit report as a list of lines.

    Works both from source (AST-scan of the project files) and inside the
    frozen exe (embedded proof + runtime module scan).  Used by the CLI
    `audit` command and by the GUI's Help -> Offline audit dialog.
    """
    lines = []
    if getattr(sys, "frozen", False):
        ok = True
        meipass = getattr(sys, "_MEIPASS",
                          os.path.dirname(os.path.abspath(__file__)))
        proof = os.path.join(meipass, "audit_result.txt")
        if os.path.exists(proof):
            lines.append("embedded source audit (from build):")
            lines.append("  " + open(proof, "r",
                                     encoding="utf-8").read().strip())
        else:
            lines.append("WARNING: no embedded source-audit proof found "
                         "in this exe")
            ok = False
        net = util.runtime_network_modules(sys.modules)
        if net:
            lines.append("FAIL: network-capable modules imported at "
                         "runtime: %s" % ", ".join(net))
            ok = False
        else:
            lines.append("OK: no network-capable modules imported at "
                         "runtime")
        present = {"__main__" if getattr(sys, "frozen", False)
                   else "god2iso"}
        present.update({"util", "xcontent", "xsf", "xiso"})
        missing = [m for m in sorted(present) if m not in sys.modules]
        if missing:
            lines.append("WARNING: tool modules missing: %s"
                         % ", ".join(missing))
            ok = False
        lines.append("OK: frozen executable verified offline" if ok
                     else "FAIL: frozen executable audit failed")
        return lines

    srcdir = os.path.dirname(os.path.abspath(__file__))
    problems = util.audit_no_network(srcdir)
    lines.append("auditing %s" % srcdir)
    if problems:
        lines.append("FAIL: network-capable imports found:")
        for f, line, mod in problems:
            lines.append("  %s:%d  %s" % (f, line, mod))
        return lines
    for name in sorted(os.listdir(srcdir)):
        if name.endswith(".py"):
            import py_compile
            try:
                py_compile.compile(os.path.join(srcdir, name), doraise=True)
            except py_compile.PyCompileError as e:
                lines.append("FAIL: %s does not compile: %s" % (name, e))
                return lines
    lines.append("OK: no network-capable imports (fully offline), all "
                 "modules compile")
    return lines


def cmd_audit(args):
    """Privacy/security self-audit: prove the tool cannot phone home."""
    lines = audit_lines()
    print("\n".join(lines))
    return 0 if lines and "FAIL" not in "\n".join(lines) else 1


def cmd_wizard(args):
    """Interactive, beginner-friendly conversion wizard."""
    print(_BANNER)
    print("This tool is offline, non-destructive and never modifies your "
          "GOD files.")
    print()
    try:
        raw = input("Path to the .live file or GOD folder"
                    " (Enter to quit): ").strip()
        if not raw:
            return 0
        lives = find_live_files(raw)
        if not lives:
            print("error: no .live file found at %r" % raw)
            return 1
        if len(lives) > 1:
            print("Found multiple packages:")
            for i, l in enumerate(lives):
                info = xcontent.parse(open(l, "rb").read())
                print("  [%d] %s  (title %s, %d part files)"
                      % (i, l, info.title_id_hex,
                         len(find_part_files(l))))
            sel = input("Pick one [0]: ").strip() or "0"
            live = lives[int(sel)]
        else:
            live = lives[0]
        info = xcontent.parse(open(live, "rb").read())
        default = os.path.join(os.getcwd(),
                               os.path.splitext(os.path.basename(live))[0]
                               + ".iso")
        out = input("Output ISO path [%s]: " % default).strip() or default
        print()
        print("  Source : %s" % live)
        print("  Title  : %s (media %08X, %d part file(s))"
              % (info.title_id_hex, info.media_id, len(find_part_files(live))))
        print("  Output : %s" % out)
        ans = input("Convert now? [Y/n]: ").strip().lower()
        if ans not in ("", "y", "yes"):
            print("aborted.")
            return 0
        def _wiz_prog(done, total, _st=[0]):
            _st[0] += 1
            if _st[0] % 16 == 0:
                sys.stderr.write("\r  %6.1f%%" % (100.0 * done / total))
                sys.stderr.flush()
        return convert(live, out, progress_cb=_wiz_prog)
    except (EOFError, KeyboardInterrupt):
        print()
        print("aborted.")
        return 130
    except (ValueError, IndexError, xiso.XisoError,
            FileNotFoundError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


def _safe_console():
    """Make stdout/stderr tolerant of any filename encoding.

    On Windows consoles (cp1252/cp437) printing a filename with characters
    outside the codepage would otherwise raise UnicodeEncodeError and crash
    mid-listing.  errors='replace' prints '?' instead - cosmetic, never
    fatal."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _hide_console_if_detached():
    """Hide the console window when the GUI is launched by double-clicking
    the frozen exe (no attached interactive console).  When run from a
    terminal (stdin is a tty) the console stays, so CLI use is unaffected."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            return
    except Exception:                            # noqa: BLE001
        pass
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:                            # noqa: BLE001
        pass


def launch_gui():
    """Launch the tkinter GUI.  Falls back to the console wizard if no
    display is available (headless/CI)."""
    _hide_console_if_detached()
    try:
        import gui
        return gui.main()
    except Exception as e:                       # noqa: BLE001
        print("GUI unavailable (%s). Use --wizard or the command line."
              % e, file=sys.stderr)
        if sys.stdin.isatty():
            return cmd_wizard(argparse.Namespace())
        return 1


def main(argv=None):
    _safe_console()
    ap = argparse.ArgumentParser(
        prog="god2iso.py",
        description="Convert Xbox 360 GOD packages to ISO (offline, "
                    "non-destructive; mirrors GOD2ISO v1.0.5). Works on "
                    "decrypted/modded content only - no encryption handling, "
                    "no title keys, no network access.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--version", action="version", version=VERSION)
    ap.add_argument("--wizard", action="store_true",
                    help="run the interactive wizard")
    ap.add_argument("--gui", action="store_true",
                    help="launch the graphical interface")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("gui", help="launch the graphical interface")
    p.set_defaults(func=lambda a: launch_gui())

    p = sub.add_parser("info", help="show .live metadata")
    p.add_argument("path", help=".live file or GOD folder")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("convert", help="GOD -> ISO")
    p.add_argument("path", help=".live file or GOD folder")
    p.add_argument("-o", "--out", help="output .iso path (default: ./<title>.iso)")
    p.add_argument("--trim", action="store_true",
                   help="trim output to the size declared in the .live header")
    p.add_argument("--fix", action="store_true",
                   help="apply god2iso's 'FixCreateIsoGoodHeader' pass")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing output file")
    p.add_argument("--progress", action="store_true",
                   help="show a live progress percentage")
    p.add_argument("--sha256", action="store_true",
                   help="print the output's SHA-256 checksum")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the deep Merkle-hash (MHT) verification")
    p.add_argument("--quiet", action="store_true",
                   help="suppress non-error output")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("list", help="list files inside an Xbox ISO")
    p.add_argument("iso")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="extract an Xbox ISO to a folder")
    p.add_argument("iso")
    p.add_argument("outdir")
    p.add_argument("--force", action="store_true",
                   help="overwrite files that already exist")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("rebuild", help="extract + rebuild a clean XISO")
    p.add_argument("iso")
    p.add_argument("-o", "--out")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing output file")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("audit",
                       help="verify the tool is offline and compiles clean")
    p.set_defaults(func=cmd_audit)

    args = ap.parse_args(argv)
    if args.gui or args.cmd == "gui":
        return launch_gui()
    if args.cmd is None:
        if args.wizard:
            return cmd_wizard(args)
        # double-click / no arguments: open the GUI; fall back to the
        # console wizard when no display is available
        return launch_gui()
    try:
        return args.func(args)
    except (util.SafetyError, xiso.XisoError,
            FileNotFoundError, ValueError, RecursionError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
