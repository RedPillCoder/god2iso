#!/usr/bin/env python3
"""Synthetic GOD package generator for testing god2iso.py.

Builds a fake GOD package with the exact layout produced by the real
iso2god-rs / GOD2ISO toolchain (verified against the open-source producers):

    <TitleID>.live                 synthetic XContent package ("LIVE")
    <TitleID>.live.data\\Data0000..  part files, each:
        [0x1000 master MHT hash list]          (SHA-1 of each sub-list)
        [0x1000 sub-hash-list 0]               (SHA-1 of each 0x1000 block)
        [0xCC000 data block 0]
        [0x1000 sub-hash-list 1]
        [0xCC000 data block 1]
        ...
        (203 sub-parts per part in real iso2god-rs; the converter is
         agnostic to the count, so tests use whatever the payload needs)

Two flavors:
  A: the data stream embeds the 0x10000-byte "XSF" header (GOD2ISO
     "hasXSF" case - classic iso2god output)
  B: the data stream starts at the ISO's LBA 32 (no XSF; GOD2ISO
     synthesizes one; .live flags at 0x391/0x395 may request sector fixing)
"""

import hashlib
import os
import struct

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xsf          # noqa: E402

SECTOR = 0x800
PART_HEADER = 0x2000          # master list + first sub-list
DATA_BLOCK = 0xCC000          # 204 x 0x1000
HASH_BLOCK = 0x1000           # up to 204 x SHA-1


def make_live(title_id=0x54455354, media_id=0x12345678, part_count=1,
              combined_size=0, vd_flags=0x00, vd_offset_raw=0,
              content_type=0x7000, mht_root=None):
    """Synthetic .live file (XContent header, metadata fields per free60 /
    iso2god-rs).  Only the first 0x400 bytes are meaningful to the parser.

    *mht_root* (20 bytes) is written at 0x37D - the Merkle tree root hash.
    When None, a placeholder is used (tests that deep-verify must pass a
    real root via godify())."""
    b = bytearray(0x5000)
    b[0:4] = b"LIVE"
    struct.pack_into(">I", b, 0x344, content_type)      # GamesOnDemand
    struct.pack_into(">I", b, 0x354, media_id)
    struct.pack_into(">I", b, 0x360, title_id)
    b[0x364] = 2                                        # platform: Xbox 360
    b[0x365] = 1                                        # executable type
    b[0x366] = 1                                        # disc number
    b[0x367] = 1                                        # disc count
    # iso2god-rs style part info
    struct.pack_into("<I", b, 0x3A0, part_count)        # sic! little-endian
    struct.pack_into(">I", b, 0x3A4, combined_size // 0x100)
    # MHT root hash + block counts (iso2god-rs con_header.rs)
    b[0x37D:0x391] = (mht_root if mht_root else
                      hashlib.sha1(b[:0x37D]).digest()[:20])
    # blocks allocated: 24-bit big-endian @0x392 (must NOT touch 0x395!)
    b[0x392:0x395] = ((combined_size // 0x1000) & 0xFFFFFF).to_bytes(3, "big")
    # GOD2ISO sector-offset-fix fields (volume descriptor region)
    b[0x391] = vd_flags
    struct.pack_into("<I", b, 0x395, vd_offset_raw)
    return bytes(b)


def _sub_list(chunk: bytes) -> bytes:
    """SHA-1 of each 0x1000 block of *chunk*, padded to 0x1000 (0-filled)."""
    out = bytearray(HASH_BLOCK)
    n = 0
    for off in range(0, len(chunk), 0x1000):
        block = chunk[off:off + 0x1000]
        out[n * 20:(n + 1) * 20] = hashlib.sha1(block).digest()
        n += 1
    return bytes(out)


def _master_list(sub_lists) -> bytes:
    """SHA-1 of each 0x1000 sub-list, padded to 0x1000 (0-filled)."""
    out = bytearray(HASH_BLOCK)
    for i, s in enumerate(sub_lists[:204]):
        out[i * 20:(i + 1) * 20] = hashlib.sha1(s).digest()
    return bytes(out)


def _build_part(payload: bytes) -> bytes:
    """One realistic part file: [master][sub0][data0][sub1][data1]..."""
    master, body = _build_part_pieces(payload)
    return master + body


def _build_part_pieces(payload: bytes):
    """Return (master_list_bytes, body_bytes) for one part.

    body = [sub0][data0][sub1][data1]...; master = SHA-1 of each sub-list
    (204 max, zero-padded to 0x1000).  The caller may append cross-part
    chain digests to the master before writing (see godify)."""
    subs = []
    body = bytearray()
    pos = 0
    while pos < len(payload):
        chunk = payload[pos:pos + DATA_BLOCK]
        sub = _sub_list(chunk)
        subs.append(sub)
        body += sub
        body += chunk
        pos += DATA_BLOCK
    return bytes(_master_list(subs)), bytes(body)


def godify(iso_bytes: bytes, out_dir: str, flavor="A",
           part_cuts=None, pad_last=0, vd_flags=0x00, vd_offset_raw=0,
           live_name="TEST0001.live", raw_payload=None):
    """Turn an ISO image into a fake GOD package.

    flavor "A": data stream = [XSF header][ISO from LBA 32]
    flavor "B": data stream = [ISO from LBA 32]  (XSF synthesized by convert)

    part_cuts: byte offsets into the *payload* at which to split parts.
    Real output is cut at period boundaries, so cuts are rounded DOWN to a
    multiple of DATA_BLOCK; each part is then interleaved independently
    (the period restarts per part, exactly like the real format).
    """
    if raw_payload is not None:
        payload = raw_payload
        if flavor == "A":
            payload = bytes(xsf.make_xsf_header(len(raw_payload))) \
                + raw_payload
    elif flavor == "A":
        hdr = xsf.make_xsf_header(len(iso_bytes))
        payload = bytes(hdr) + iso_bytes[xsf.XSF_SIZE:]
    elif flavor == "B":
        payload = iso_bytes[xsf.XSF_SIZE:]
    else:
        raise ValueError(flavor)

    cuts = sorted(part_cuts or [])
    cuts = [c - (c % DATA_BLOCK) for c in cuts if c > 0]
    cuts = [c for c in cuts if 0 < c < len(payload)]
    cuts.append(len(payload))
    slices = []
    start = 0
    for c in cuts:
        slices.append(payload[start:c])
        start = c

    # build each part, then chain the Merkle tree exactly like iso2god-rs:
    #   part i's master list gets the digest of part i+1's master appended
    #   (after its own sub-list hashes), and the .live root = digest of
    #   part 0's final master list.
    masters = []
    bodies = []
    for sl in slices:
        m, b = _build_part_pieces(sl)
        masters.append(bytearray(m))
        bodies.append(b)
    for i in range(len(masters) - 2, -1, -1):
        n_subs = len(bodies[i]) // (DATA_BLOCK + HASH_BLOCK)
        pos = n_subs * 20
        masters[i][pos:pos + 20] = hashlib.sha1(bytes(masters[i + 1])).digest()
    mht_root = hashlib.sha1(bytes(masters[0])).digest() if masters else None

    part_paths = []
    for i, sl in enumerate(slices):
        body = bytes(masters[i]) + bodies[i]
        if i == len(slices) - 1 and pad_last:
            body += bytes(pad_last)
        path = os.path.join(out_dir, "%s.data" % live_name,
                            "Data%04d" % i)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(body)
        part_paths.append(path)

    live = make_live(part_count=len(slices), combined_size=len(payload),
                     vd_flags=vd_flags, vd_offset_raw=vd_offset_raw,
                     mht_root=mht_root)
    live_path = os.path.join(out_dir, live_name)
    with open(live_path, "wb") as f:
        f.write(live)
    return live_path, part_paths
