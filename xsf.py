#!/usr/bin/env python3
"""The "XSF" header: a 32-sector (0x10000-byte) block that sits at the start
of a GOD data stream (or is synthesized by GOD2ISO) and fills the region that
a retail disc reserves for its security sectors (DMI/PFI/SS).

Reference: god2iso (raburton) - `XSFHeader.bin` resource + `Form1.cs`
(`FixXFSHeader`).  Field layout (all little-endian):

    offset  size  meaning
    ------  ----  -------
    0x000   4     magic "XSF\\x1A"
    0x004   4     constant 0x400
    0x008   8     data length - 0x400        (patched after assembly)
    0x7A69  var   creator string (informational)
    0x8050  4     total sectors (LE)         (patched after assembly)
    0x8054  4     total sectors (BE)
    rest         zero padding to 0x10000

The template's 0x08 placeholder bytes literally read "SIZE-64L" in the
upstream binary; we write our own placeholder and patch it the same way.
"""

import struct

XSF_MAGIC = b"XSF\x1a"
XSF_SIZE = 0x10000
SECTOR_SIZE = 0x800

#: value written by god2iso at 0x04 (meaning unknown, keep byte-compatible)
XSF_UNKNOWN_FIELD = 0x400

CREATOR = b"god2iso.py (format-compatible with God2Iso v1.0.5)"


def make_xsf_header(total_len: int | None = None) -> bytearray:
    """Build a pristine XSF header template.

    If *total_len* is given the size fields are patched immediately,
    otherwise the caller must call :func:`patch_xsf_header` afterwards
    (mirroring god2iso, which patches after the data has been written).
    """
    buf = bytearray(XSF_SIZE)
    buf[0:4] = XSF_MAGIC
    struct.pack_into("<I", buf, 0x04, XSF_UNKNOWN_FIELD)
    struct.pack_into("<Q", buf, 0x08, 0)                 # patched later
    buf[0x7A69:0x7A69 + len(CREATOR)] = CREATOR
    struct.pack_into("<I", buf, 0x8050, 0)               # patched later
    struct.pack_into(">I", buf, 0x8054, 0)               # patched later
    if total_len is not None:
        patch_xsf_header(buf, total_len)
    return buf


def patch_xsf_header(buf: bytearray, total_len: int) -> None:
    """Fill the size fields, exactly like god2iso's FixXFSHeader:
      * u64 @0x08  = total_len - 0x400
      * u32 LE+BE  @0x8050 = total_len // 2048
    """
    if len(buf) < XSF_SIZE:
        raise ValueError("XSF header buffer too small")
    struct.pack_into("<Q", buf, 0x08, total_len - 0x400)
    struct.pack_into("<I", buf, 0x8050, total_len // SECTOR_SIZE)
    struct.pack_into(">I", buf, 0x8054, total_len // SECTOR_SIZE)


def looks_like_xsf_header(buf: bytes) -> bool:
    """True if *buf* begins with the XSF magic."""
    return buf[:4] == XSF_MAGIC


def embedded_size_ok(buf: bytes, total_len: int) -> bool:
    """Check that an embedded XSF header's size field matches *total_len*."""
    if len(buf) < 0x10:
        return False
    return struct.unpack_from("<Q", buf, 0x08)[0] == total_len - 0x400
