#!/usr/bin/env python3
"""Parser for the Xbox 360 XContent / STFS header (as found in GOD ".live" files).

Format references:
  - free60 wiki "STFS" (metadata offsets 0x340..0x1719)
  - iso2god-rs `src/god/con_header.rs` (how a GOD .live is written)
  - god2iso `Form1.cs` (fields at 0x391 / 0x395 used for sector-offset fixing)

The .live file is a small STFS-style package (magic "LIVE" for remotely
signed content, "CON " for console-signed, "PIRS" for pirated-content... i.e.
"PIRS" for PIRS-signed).  It carries the game metadata: Title ID, Media ID,
content type, part count and combined data size -- plus a 0x24-byte
"volume descriptor" region (0x379) with GOD-specific fields.

NOTE: this module only *parses metadata*.  It contains no cryptography.
"""

import struct

MAGIC_LIVE = b"LIVE"
MAGIC_CON = b"CON "
MAGIC_PIRS = b"PIRS"

CONTENT_TYPE_GOD = 0x7000      # Games on Demand
CONTENT_TYPE_XBOX_ORIGINAL = 0x5000


def _u16be(b, o): return struct.unpack_from(">H", b, o)[0]
def _u32be(b, o): return struct.unpack_from(">I", b, o)[0]
def _u64be(b, o): return struct.unpack_from(">Q", b, o)[0]
def _u32le(b, o): return struct.unpack_from("<I", b, o)[0]
def _u64le(b, o): return struct.unpack_from("<Q", b, o)[0]


class XContentInfo:
    """Metadata parsed from a GOD .live file."""

    def __init__(self):
        self.magic = b""
        self.magic_name = ""
        self.title_id = 0
        self.media_id = 0
        self.content_type = 0
        self.platform = 0
        self.executable_type = 0
        self.disc_number = 0
        self.disc_count = 0
        self.header_size = 0
        self.content_size = 0
        self.save_game_id = 0
        self.console_id = b""
        self.profile_id = b""
        self.volume_descriptor = b""       # 0x24 bytes @ 0x379
        self.vd_flags = 0                  # byte @ 0x391 (0x40 bit => offset present)
        self.vd_offset_raw = 0             # u32 LE @ 0x395 (see god2iso FixSectorOffsets)
        self.blocks_allocated = 0          # u24 BE @ 0x392 (iso2god-rs)
        self.mht_root_hash = b""           # 20 bytes @ 0x37D (Merkle tree root)
        self.part_count_candidates = []    # (offset, endian, value)
        self.combined_size_candidates = []  # (offset, endian, scale, value)

    @property
    def title_id_hex(self):
        return "%08X" % self.title_id

    def summary(self):
        return (
            "magic           : %r (%s)\n"
            "title id        : %s\n"
            "media id        : %08X\n"
            "content type    : 0x%04X (%s)\n"
            "platform        : %d\n"
            "executable type : %d\n"
            "disc            : %d of %d\n"
            "header size     : 0x%X\n"
            "content size    : %d bytes\n"
            "save game id    : %08X\n"
            "console id      : %s\n"
            "profile id      : %s\n"
            "vd_flags(0x391) : 0x%02X\n"
            "vd_offset(0x395): %d (god2iso correction: %d)\n"
            "blocks alloc    : %d (u24 BE @0x392)\n"
            "mht root hash   : %s\n"
            "part counts     : %s\n"
            "combined sizes  : %s"
            % (
                self.magic, self.magic_name,
                self.title_id_hex,
                self.media_id,
                self.content_type,
                "GamesOnDemand" if self.content_type == CONTENT_TYPE_GOD
                else ("XboxOriginal" if self.content_type == CONTENT_TYPE_XBOX_ORIGINAL
                      else "unknown"),
                self.platform,
                self.executable_type,
                self.disc_number, self.disc_count,
                self.header_size,
                self.content_size,
                self.save_game_id,
                self.console_id.hex() if self.console_id else "(none)",
                self.profile_id.hex() if self.profile_id else "(none)",
                self.vd_flags,
                self.vd_offset_raw,
                2 * self.vd_offset_raw - 34,
                self.blocks_allocated,
                self.mht_root_hash.hex() if self.mht_root_hash else "(none)",
                [(hex(o), e, v) for o, e, v in self.part_count_candidates],
                [(hex(o), e, s, v) for o, e, s, v in self.combined_size_candidates],
            )
        )


def parse(data: bytes) -> XContentInfo:
    if len(data) < 0x400:
        raise ValueError("file too small to be an XContent package (%d bytes)" % len(data))

    info = XContentInfo()
    info.magic = data[0:4]
    if info.magic == MAGIC_LIVE:
        info.magic_name = "remotely signed (LIVE) - standard GOD"
    elif info.magic == MAGIC_CON:
        info.magic_name = "console signed (CON )"
    elif info.magic == MAGIC_PIRS:
        info.magic_name = "PIRS signed"
    else:
        raise ValueError("not an XContent package: magic %r (want 'LIVE'/'CON '/'PIRS')" % info.magic)

    info.header_size = _u32be(data, 0x340)
    info.content_type = _u32be(data, 0x344)
    info.content_size = _u64be(data, 0x34C)
    info.media_id = _u32be(data, 0x354)
    info.title_id = _u32be(data, 0x360)
    info.platform = data[0x364]
    info.executable_type = data[0x365]
    info.disc_number = data[0x366]
    info.disc_count = data[0x367]
    info.save_game_id = _u32be(data, 0x368)
    info.console_id = data[0x36C:0x371]
    info.profile_id = data[0x371:0x379]
    info.volume_descriptor = data[0x379:0x39D]
    info.vd_flags = data[0x391]
    info.vd_offset_raw = _u32le(data, 0x395)
    info.blocks_allocated = (data[0x392] << 16) | (data[0x393] << 8) | data[0x394]
    info.mht_root_hash = data[0x37D:0x391]

    # --- part count / combined data size ---------------------------------
    # The field locations differ between builders:
    #   * free60 wiki:    u32 count @0x39D (BE), s64 combined size @0x3A1 (BE)
    #   * iso2god-rs:     u32 count @0x3A0 (LE, sic!), u32 size/0x100 @0x3A4 (BE)
    # We record every plausible reading; callers validate against reality.
    info.part_count_candidates = [
        (0x39D, "be", _u32be(data, 0x39D)),
        (0x3A0, "le", _u32le(data, 0x3A0)),
        (0x3A0, "be", _u32be(data, 0x3A0)),
    ]
    info.combined_size_candidates = [
        (0x3A1, "be", 1, _u64be(data, 0x3A1)),
        (0x3A4, "be", 0x100, _u32be(data, 0x3A4) * 0x100),
        (0x3A1, "le", 1, _u64le(data, 0x3A1)),
    ]
    return info


def pick_part_count(info: XContentInfo, observed: int) -> int:
    """Choose the header's part-count field that matches the on-disk part count."""
    for off, end, val in info.part_count_candidates:
        if val == observed:
            return val
    for off, end, val in info.part_count_candidates:
        if 0 < val <= observed + 2:
            return val
    return observed


def pick_combined_size(info: XContentInfo, output_len: int) -> int:
    """Choose the header's combined-data-size field that is sane for output_len.

    Returns 0 if no candidate is sane.
    """
    best = 0
    for off, end, scale, val in info.combined_size_candidates:
        if 0 < val <= output_len:
            best = max(best, val)
    return best
