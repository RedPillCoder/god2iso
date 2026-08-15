#!/usr/bin/env python3
"""XDVDFS ("XISO") reader and writer for Xbox / Xbox 360 disc images.

Format references:
  - xboxdevwiki.net/XDVDFS (volume descriptor at sector 32)
  - extract-xiso (XboxDev) - the reference C implementation
    (entry layout, subtree offsets, ISO9660 "burnability" descriptors)
  - god2iso (raburton) Form1.cs (same entry walk used for offset fixing)

Layout (2048-byte sectors, little-endian fields):
  * ISO9660 PVD  @ 0x8000 (optional, for burn-tool auto-detection)
  * XDVDFS volume descriptor @ 0x10000 (LBA 32):
       0x00  "MICROSOFT*XBOX*MEDIA" (20 bytes)
       0x14  u32 root directory table sector
       0x18  u32 root directory table size (multiple of 0x800)
       0x1C  u64 FILETIME timestamp
       0x7EC "MICROSOFT*XBOX*MEDIA" (trailing magic)
  * directory table entry (14 + namelen, padded to 4 with 0xFF):
       u16 left  subtree offset (in dwords from table start; 0xFFFF = pad/end)
       u16 right subtree offset (dwords)
       u32 start sector, u32 size, u8 attributes, u8 name length, name
     attributes: 0x10 = directory, 0x20 = archive, 0x01/0x02/0x04/0x80 ...
  * tables are padded to sector boundaries with 0xFF; an empty table is a
    sector of 0xFF; entries never straddle sector boundaries.
"""

import os
import struct
import tempfile
import time

SECTOR_SIZE = 0x800
XDVDFS_MAGIC = b"MICROSOFT*XBOX*MEDIA"            # 20 bytes
VOLUME_DESCRIPTOR_OFFSET = 0x10000                # LBA 32
ROOT_DEFAULT_SECTOR = 0x108
FILE_MODULUS = 0x10000

ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_NORMAL = 0x80

PAD_BYTE = 0xFF
PAD_SHORT = 0xFFFF

# ISO9660 (ECMA-119) descriptor offsets used by extract-xiso
ECMA_119_DATA_AREA_START = 0x8000
ECMA_119_VOLUME_SPACE_SIZE = 0x8000 + 80
ECMA_119_VOLUME_SET_SIZE = 0x8000 + 120
ECMA_119_VOLUME_SET_IDENTIFIER = 0x8000 + 190
ECMA_119_VOLUME_CREATION_DATE = 0x8000 + 813


class XisoError(Exception):
    pass


class Entry:
    __slots__ = ("name", "sector", "size", "attrib", "l_off", "r_off")

    def __init__(self, name, sector, size, attrib, l_off=0, r_off=0):
        self.name = name
        self.sector = sector
        self.size = size
        self.attrib = attrib
        self.l_off = l_off
        self.r_off = r_off

    @property
    def is_dir(self):
        return bool(self.attrib & ATTR_DIRECTORY)

    def __repr__(self):
        return "Entry(%r, sector=%d, size=%d, attrib=0x%02X)" % (
            self.name, self.sector, self.size, self.attrib)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def _u16le(b, o): return struct.unpack_from("<H", b, o)[0]
def _u32le(b, o): return struct.unpack_from("<I", b, o)[0]
def _u64le(b, o): return struct.unpack_from("<Q", b, o)[0]


def read_volume_descriptor(f, lseek_offset=0):
    """Return (root_sector, root_size, timestamp) or raise XisoError.

    *lseek_offset* is the file offset of the game partition start (0 for a
    trimmed XISO; 0xFD90000/0x2080000-style for full XGD2/XGD3 images)."""
    f.seek(lseek_offset + VOLUME_DESCRIPTOR_OFFSET)
    sector = f.read(SECTOR_SIZE)
    if len(sector) < SECTOR_SIZE:
        raise XisoError("image too small for a volume descriptor")
    if sector[0:20] != XDVDFS_MAGIC:
        raise XisoError("no XDVDFS volume descriptor at 0x%X "
                        "(not an Xbox ISO, or the data is encrypted)"
                        % VOLUME_DESCRIPTOR_OFFSET)
    if sector[0x7EC:0x7EC + 20] != XDVDFS_MAGIC:
        raise XisoError("volume descriptor trailing magic missing (corrupt image)")
    root_sector = _u32le(sector, 0x14)
    root_size = _u32le(sector, 0x18)
    ts = _u64le(sector, 0x1C)
    if root_size == 0:
        raise XisoError("volume descriptor declares an empty root table")
    return root_sector, root_size, ts


MAX_TABLE_SIZE = 64 << 20        # sanity cap: real dir tables are < 1 MB
MAX_TREE_DEPTH = 4096            # entry-tree depth guard (subtree pointers)
MAX_DIR_DEPTH = 128              # directory nesting guard


def walk_table(data: bytes):
    """Yield Entry objects from one directory table, following left/right
    subtree pointers exactly like extract-xiso's traverse_xiso().

    Entries are yielded in the order the reference tool reads them.
    Bounded: cycle guard + depth limit so a corrupt table can never
    recurse forever."""
    n = len(data)
    seen = set()

    def read_entry(pos, entries, depth=0):
        if depth > MAX_TREE_DEPTH:
            raise XisoError("directory table tree too deep (corrupt image)")
        while True:
            if pos + 4 > n:
                return pos
            if pos in seen:                       # guard against malformed cycles
                return pos
            seen.add(pos)
            tmp = _u16le(data, pos)
            if tmp == PAD_SHORT:                  # 0xFFFF: pad / end marker
                if pos == 0:
                    return pos                    # empty directory
                pos = ((pos // SECTOR_SIZE) + 1) * SECTOR_SIZE
                continue
            l_off = tmp
            r_off = _u16le(data, pos + 2)
            sector = _u32le(data, pos + 4)
            size = _u32le(data, pos + 8)
            attrib = data[pos + 12]
            namelen = data[pos + 13]
            if pos + 14 + namelen > n:
                raise XisoError("directory entry overruns table")
            name = data[pos + 14:pos + 14 + namelen]
            try:
                name = name.decode("ascii")
            except UnicodeDecodeError:
                name = name.decode("latin-1")
            entries.append(Entry(name, sector, size, attrib, l_off, r_off))
            entry_len = 14 + namelen
            entry_len += (4 - entry_len % 4) % 4
            if l_off:
                pos = read_entry(l_off * 4, entries, depth + 1)   # left subtree
            else:
                pos += entry_len
            if r_off:
                pos = read_entry(r_off * 4, entries, depth + 1)   # right subtree
            # loop continues sequentially from wherever the subtrees ended

    entries = []
    read_entry(0, entries)
    return entries


def read_table(f, sector, size, lseek_offset=0):
    if size > MAX_TABLE_SIZE:
        raise XisoError("directory table at sector %d absurdly large "
                        "(%d bytes) - corrupt image" % (sector, size))
    f.seek(lseek_offset + sector * SECTOR_SIZE)
    data = f.read(size)
    if len(data) < size:
        raise XisoError("directory table at sector %d truncated (need %d bytes)"
                        % (sector, size))
    return walk_table(data)


def read_file(f, sector, size, lseek_offset=0) -> bytes:
    f.seek(lseek_offset + sector * SECTOR_SIZE)
    data = f.read(size)
    if len(data) < size:
        raise XisoError("file at sector %d truncated (need %d bytes, got %d)"
                        % (sector, size, len(data)))
    return data


def read_file_to(f, sector, size, out, lseek_offset=0, chunk=1 << 20):
    """Stream *size* bytes from *f* at *sector* into file object *out*.

    Memory-bounded copy (1 MB chunks) so multi-GB files never need to be
    held in RAM."""
    f.seek(lseek_offset + sector * SECTOR_SIZE)
    remaining = size
    while remaining:
        buf = f.read(min(chunk, remaining))
        if not buf:
            raise XisoError("file at sector %d truncated (need %d bytes)"
                            % (sector, size))
        out.write(buf)
        remaining -= len(buf)
    return size


def _check_name(name: str):
    """Validate a single table entry name coming from the (untrusted) image.

    Rejects path traversal, separators, NUL/control characters and Windows
    reserved-name hazards (trailing dot/space).  Raises XisoError.
    """
    if name in (".", ".."):
        raise XisoError("unsafe filename %r" % name)
    if "/" in name or "\\" in name or "\x00" in name:
        raise XisoError("unsafe filename %r (separator/NUL)" % name)
    if any(c < " " or c == "\x7f" for c in name):
        raise XisoError("unsafe filename %r (control character)" % name)
    if name.endswith((" ", ".")):
        raise XisoError("unsafe filename %r (trailing space/dot)" % name)
    if name in ("CON", "PRN", "AUX", "NUL",
                "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
                "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
                "LPT6", "LPT7", "LPT8", "LPT9"):
        raise XisoError("unsafe filename %r (Windows reserved device name)" % name)


def list_image(path, lseek_offset=0) -> list:
    """Return [(relpath, is_dir, size)] for everything in the ISO."""
    out = []
    with open(path, "rb") as f:
        root_sector, root_size, _ = read_volume_descriptor(f, lseek_offset)

        def walk(sector, size, prefix, depth=0):
            if depth > MAX_DIR_DEPTH:
                raise XisoError("directory nesting too deep (corrupt image)")
            for e in read_table(f, sector, size, lseek_offset):
                _check_name(e.name)
                rel = prefix + e.name
                out.append((rel, e.is_dir, e.size))
                if e.is_dir:
                    walk(e.sector, e.size, rel + "/", depth + 1)

        walk(root_sector, root_size, "")
    return out


def extract_image(path, outdir, force=False, warn=None, lseek_offset=0):
    """Extract every file under *outdir*; returns [(relpath, size)].

    Safety policy (see util.py):
      * every destination is validated with safe_join - entries can never
        escape *outdir*;
      * symlinks in the extraction tree are refused (no symlink escapes);
      * case-insensitive name collisions (hazard on Windows/macOS) are
        detected and refused;
      * existing files are never overwritten unless *force* is True, and
        nothing outside the extraction set is ever touched;
      * file data is streamed (never fully loaded into memory);
      * directory nesting is depth-limited.
    """
    import util
    warn = warn or (lambda msg: None)
    outdir_abs = os.path.abspath(outdir)
    os.makedirs(outdir_abs, exist_ok=True)
    util.ensure_not_symlink(outdir_abs, "output directory")
    seen_lower = {}
    written = []
    with open(path, "rb") as f:
        root_sector, root_size, _ = read_volume_descriptor(f, lseek_offset)

        def walk(sector, size, prefix, depth=0):
            if depth > MAX_DIR_DEPTH:
                raise XisoError("directory nesting too deep (corrupt image)")
            for e in read_table(f, sector, size, lseek_offset):
                _check_name(e.name)
                rel = prefix + e.name
                key = rel.lower()
                if key in seen_lower:
                    raise XisoError("case-insensitive collision: %r vs %r "
                                    "(unsafe on Windows/macOS)"
                                    % (seen_lower[key], rel))
                seen_lower[key] = rel
                if e.is_dir:
                    walk(e.sector, e.size, rel + "/", depth + 1)
                    continue
                # build parent directories step by step, refusing symlinks
                parts = rel.split("/")
                cur = outdir_abs
                for p in parts[:-1]:
                    cur = util.safe_join(cur, p)
                    util.ensure_not_symlink(cur, "directory")
                    os.makedirs(cur, exist_ok=True)
                dst = util.safe_join(cur, parts[-1])
                util.ensure_not_symlink(dst, "file")
                if os.path.exists(dst) and not force:
                    raise XisoError("refusing to overwrite existing file %r "
                                    "(use --force to overwrite)" % dst)
                # atomic per-file write: temp + rename, streamed
                fd, tmp = tempfile.mkstemp(prefix=".god2iso-x-", suffix=".tmp",
                                           dir=os.path.dirname(dst) or ".")
                try:
                    with os.fdopen(fd, "wb") as w:
                        read_file_to(f, e.sector, e.size, w, lseek_offset)
                    os.replace(tmp, dst)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                written.append((rel, e.size))

        walk(root_sector, root_size, "")
    return written


def find_default_xex(path, lseek_offset=0):
    for rel, is_dir, size in list_image(path, lseek_offset):
        if not is_dir and rel.lower() == "default.xex":
            return rel
    return None


def find_xdvdfs_offset(path, limit=64 << 20):
    """Locate the XDVDFS volume-descriptor magic in *path*.

    Returns the byte offset of the first occurrence (typically 0x10000 for
    trimmed images, or partition_start + 0x10000 for full XGD2/XGD3
    images), or -1 if not found within the first *limit* bytes.  A missing
    marker means the data is not a plaintext disc image (encrypted)."""
    try:
        with open(path, "rb") as f:
            buf = f.read(limit)
    except OSError:
        return -1
    return buf.find(XDVDFS_MAGIC)


def partition_offset(path):
    """Game-partition start offset for an image, auto-detected."""
    off = find_xdvdfs_offset(path)
    if off >= VOLUME_DESCRIPTOR_OFFSET:
        return off - VOLUME_DESCRIPTOR_OFFSET
    return 0


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _filetime_now() -> int:
    """Windows FILETIME (100 ns since 1601-01-01) for 'now'."""
    return int((time.time() + 11644473600.0) * 10_000_000)


class _Node:
    __slots__ = ("name", "sector", "size", "attrib", "offset",
                 "left", "right", "table_bytes", "children", "blob")

    def __init__(self, name, sector=0, size=0, attrib=0):
        self.name = name
        self.sector = sector
        self.size = size
        self.attrib = attrib
        self.offset = 0
        self.left = None
        self.right = None
        self.table_bytes = None
        self.children = None      # list of _Node (for directories)
        self.blob = None          # file bytes (for files)


def _build_tree(entries: list):
    """Balanced binary tree from a sorted list (midpoint split; equivalent
    to extract-xiso's AVL without rebalancing)."""
    if not entries:
        return None

    def build(lo, hi):
        if lo >= hi:
            return None
        mid = (lo + hi) // 2
        node = entries[mid]
        node.left = build(lo, mid)
        node.right = build(mid + 1, hi)
        return node

    return build(0, len(entries))


def _table_size(entries: list) -> int:
    """Byte size of a directory table for *entries* (sector aligned).

    Only the names matter, so this can run before sectors are allocated.
    Must produce the same layout as :func:`_encode_table`.
    """
    if not entries:
        return SECTOR_SIZE
    sorted_entries = sorted(entries, key=lambda e: e.name.lower())
    root = _build_tree(sorted_entries)
    cur = 0

    def assign(node):
        nonlocal cur
        length = 14 + len(node.name)
        length += (4 - length % 4) % 4
        if (cur // SECTOR_SIZE) != ((cur + length - 1) // SECTOR_SIZE):
            cur = ((cur // SECTOR_SIZE) + 1) * SECTOR_SIZE   # no straddling
        node.offset = cur
        cur += length
        if node.left:
            assign(node.left)
        if node.right:
            assign(node.right)

    assign(root)
    return ((cur + SECTOR_SIZE - 1) // SECTOR_SIZE) * SECTOR_SIZE


def _encode_table(entries: list) -> bytes:
    """Encode one directory table: sorted balanced tree, prefix-order
    offsets, 0xFF padding, sector aligned.  Mirrors extract-xiso's
    calculate_directory_size / write_directory.  Sector fields must already
    be allocated on the entry nodes."""
    if not entries:
        return bytes([PAD_BYTE]) * SECTOR_SIZE      # empty table

    sorted_entries = sorted(entries, key=lambda e: e.name.lower())
    root = _build_tree(sorted_entries)

    cur = 0

    def assign(node):
        nonlocal cur
        length = 14 + len(node.name)
        length += (4 - length % 4) % 4
        if (cur // SECTOR_SIZE) != ((cur + length - 1) // SECTOR_SIZE):
            cur = ((cur // SECTOR_SIZE) + 1) * SECTOR_SIZE   # no straddling
        node.offset = cur
        cur += length
        if node.left:
            assign(node.left)
        if node.right:
            assign(node.right)

    assign(root)
    table_size = ((cur + SECTOR_SIZE - 1) // SECTOR_SIZE) * SECTOR_SIZE
    table = bytearray([PAD_BYTE]) * table_size

    def write_node(node):
        l_off = node.left.offset // 4 if node.left else 0
        r_off = node.right.offset // 4 if node.right else 0
        name = node.name.encode("ascii")
        if len(name) > 255:
            raise XisoError("filename too long: %r" % node.name)
        o = node.offset
        struct.pack_into("<H", table, o, l_off)
        struct.pack_into("<H", table, o + 2, r_off)
        struct.pack_into("<I", table, o + 4, node.sector)
        struct.pack_into("<I", table, o + 8, node.size)
        table[o + 12] = node.attrib
        table[o + 13] = len(name)
        table[o + 14:o + 14 + len(name)] = name
        if node.left:
            write_node(node.left)
        if node.right:
            write_node(node.right)

    write_node(root)
    return bytes(table)


def build_image(file_map: dict, out_path: str,
                root_sector: int = ROOT_DEFAULT_SECTOR):
    """Build an XDVDFS image from {relpath: bytes-or-path} (dirs implicit).

    Each value may be file *bytes* or a *path string* to a file on disk;
    path values are streamed during the write phase (memory-bounded, so a
    multi-GB rebuild never needs to hold the data in RAM).

    Mirrors extract-xiso's create mode: zeroed 0x10000 header region,
    ISO9660 PVD at 0x8000, XDVDFS descriptor at 0x10000, root table at
    0x108, directory tables then file data in prefix order, 0xFF padding,
    total size padded to a 0x10000 multiple.
    """
    # --- build the directory tree -------------------------------------------
    root = _Node("", attrib=ATTR_DIRECTORY)

    for relpath, blob in sorted(file_map.items()):
        parts = relpath.replace("\\", "/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise XisoError("bad path: %r" % relpath)
        size = (os.path.getsize(blob) if isinstance(blob, str)
                else len(blob))
        node = root
        for depth, part in enumerate(parts):
            is_last = depth == len(parts) - 1
            if node.children is None:
                node.children = []
            child = next((c for c in node.children if c.name == part), None)
            if child is None:
                child = _Node(part, size=size if is_last else 0,
                              attrib=ATTR_ARCHIVE if is_last else ATTR_DIRECTORY)
                node.children.append(child)
            node = child
            if is_last:
                node.blob = blob

    # --- compute table sizes (names only; before allocation) -----------------
    def compute_sizes(node):
        if node.children is None:
            node.size = SECTOR_SIZE
            return
        for c in node.children:
            if c.attrib & ATTR_DIRECTORY:
                compute_sizes(c)
                c.size = _table_size(c.children)
        node.size = _table_size(node.children)

    compute_sizes(root)

    # --- allocate sectors (mirror calculate_directory_offsets) ---------------
    cursor = root_sector

    def allocate(node):
        nonlocal cursor
        # this node's own table occupies [cursor, cursor + table size)
        node.sector = cursor
        cursor += (node.size + SECTOR_SIZE - 1) // SECTOR_SIZE
        for c in sorted(node.children or [], key=lambda e: e.name.lower()):
            if not (c.attrib & ATTR_DIRECTORY):
                c.sector = cursor
                cursor += (c.size + SECTOR_SIZE - 1) // SECTOR_SIZE
        for c in sorted(node.children or [], key=lambda e: e.name.lower()):
            if c.attrib & ATTR_DIRECTORY:
                allocate(c)

    allocate(root)
    assert root.sector == root_sector

    # --- encode tables (sector fields now final) ------------------------------
    def encode_tables(node):
        node.table_bytes = _encode_table(node.children)
        for c in node.children or []:
            if c.attrib & ATTR_DIRECTORY:
                encode_tables(c)

    encode_tables(root)

    # --- write the image ------------------------------------------------------
    with open(out_path, "wb") as f:
        f.write(bytes(FILE_MODULUS))                  # zeroed header region

        def write_files(node):
            for c in sorted(node.children, key=lambda e: e.name.lower()):
                if not (c.attrib & ATTR_DIRECTORY):
                    f.seek(c.sector * SECTOR_SIZE)
                    blob = c.blob
                    if isinstance(blob, str):
                        # stream from a file on disk (memory-bounded)
                        with open(blob, "rb") as src:
                            while True:
                                buf = src.read(1 << 20)
                                if not buf:
                                    break
                                f.write(buf)
                    else:
                        f.write(blob)
                    pad = (-c.size) % SECTOR_SIZE
                    if pad:
                        f.write(bytes([PAD_BYTE]) * pad)
            for c in sorted(node.children, key=lambda e: e.name.lower()):
                if c.attrib & ATTR_DIRECTORY:
                    write_files(c)

        write_files(root)

        def write_tables(node):
            f.seek(node.sector * SECTOR_SIZE)
            f.write(node.table_bytes)
            for c in sorted(node.children, key=lambda e: e.name.lower()):
                if c.attrib & ATTR_DIRECTORY:
                    write_tables(c)

        write_tables(root)

        # XDVDFS volume descriptor @ 0x10000
        f.seek(VOLUME_DESCRIPTOR_OFFSET)
        vd = bytearray(SECTOR_SIZE)
        vd[0:20] = XDVDFS_MAGIC
        struct.pack_into("<I", vd, 0x14, root_sector)
        struct.pack_into("<I", vd, 0x18, root.size)
        struct.pack_into("<Q", vd, 0x1C, _filetime_now())
        vd[0x7EC:0x7EC + 20] = XDVDFS_MAGIC
        f.write(vd)

        # pad to FILE_MODULUS, then patch the ISO9660 PVD with the total size
        end = f.tell()
        pad = (-end) % FILE_MODULUS
        if pad:
            f.write(bytes(pad))
        total_sectors = (end + pad) // SECTOR_SIZE

        f.seek(ECMA_119_DATA_AREA_START)
        f.write(b"\x01" + b"CD001" + b"\x01")                # PVD
        f.seek(ECMA_119_VOLUME_SPACE_SIZE)
        f.write(struct.pack("<I", total_sectors))
        f.write(struct.pack(">I", total_sectors))
        f.seek(ECMA_119_VOLUME_SET_SIZE)
        f.write(b"\x01\x00\x00\x01\x01\x00\x00\x01\x00\x08\x08\x00")
        f.seek(ECMA_119_VOLUME_SET_IDENTIFIER)
        spaces = bytes([0x20]) * (ECMA_119_VOLUME_CREATION_DATE
                                  - ECMA_119_VOLUME_SET_IDENTIFIER)
        date = b"0000000000000000"
        f.write(spaces)
        for _ in range(4):
            f.write(date)
        f.write(b"\x01")
        f.seek(ECMA_119_DATA_AREA_START + SECTOR_SIZE)
        f.write(b"\xff" + b"CD001" + b"\x01")                # terminator

    return out_path
