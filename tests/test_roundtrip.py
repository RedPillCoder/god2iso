#!/usr/bin/env python3
"""Round-trip tests for god2iso.py against synthetic GOD packages.

Run:  python3 tests/test_roundtrip.py
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xcontent        # noqa: E402
import xsf             # noqa: E402
import xiso            # noqa: E402
import make_fake_god   # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "god2iso.py")

SECTOR = 0x800


def make_file_map(n_media=300):
    """A small game-like tree: default.xex etc., a big media dir (spans
    several table sectors) and nested subdirectories.  ~2.2 MB total."""
    files = {}
    files["default.xex"] = bytes(range(256)) * 4096       # 1 MB
    files["default_mp.xex"] = b"MP" * (128 * 1024)        # 256 KB
    files["readme.txt"] = b"hello god2iso\n" * 400
    files["media/logo.png"] = b"\x89PNG fake" + bytes(2048)
    files["media/shaders/vert.hlsl"] = b"shader" * 1024
    for i in range(n_media):
        files["media/tex_%04d.bin" % i] = bytes([i % 251]) * ((i % 29 + 1) * 128)
    return files


def run_tool(*argv):
    return subprocess.run([sys.executable, TOOL, *argv],
                          capture_output=True, text=True)


def inflate_table_fields(iso_bytes, delta):
    """Return a copy of the ISO whose directory-table sector fields are all
    *delta* larger than the actual data position - i.e. the situation god2iso's
    FixSectorOffsets corrects (fields written in a larger coordinate space,
    e.g. full-disc LBAs, than the stored partition)."""
    data = bytearray(iso_bytes)
    SECTOR = xiso.SECTOR_SIZE

    def walk(sector, size):
        pos = sector * SECTOR
        end = pos + size
        while pos + 4 < end:
            if (pos + 4) // SECTOR > pos // SECTOR:
                pos += SECTOR - (pos % SECTOR)
            u32 = struct.unpack_from("<I", data, pos)[0]
            if u32 == 0xFFFFFFFF:
                if end - (pos + 4) > SECTOR:
                    pos += SECTOR - (pos % SECTOR)
                    continue
                break
            sec = struct.unpack_from("<I", data, pos + 4)[0]
            if sec > 0:
                struct.pack_into("<I", data, pos + 4, sec + delta)
            size_ = struct.unpack_from("<I", data, pos + 8)[0]
            attrib = data[pos + 12]
            if attrib & xiso.ATTR_DIRECTORY:
                walk(sec, size_)               # actual table position
            namelen = data[pos + 13]
            pos += 14 + namelen
            if (14 + namelen) % 4:
                pos += 4 - ((14 + namelen) % 4)

    rs = struct.unpack_from("<I", data, xiso.VOLUME_DESCRIPTOR_OFFSET + 0x14)[0]
    rsz = struct.unpack_from("<I", data, xiso.VOLUME_DESCRIPTOR_OFFSET + 0x18)[0]
    struct.pack_into("<I", data, xiso.VOLUME_DESCRIPTOR_OFFSET + 0x14, rs + delta)
    walk(rs, rsz)          # walk at the ACTUAL table positions
    return bytes(data)


class God2IsoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="god2iso_test_")
        cls.files = make_file_map()
        cls.iso = os.path.join(cls.tmp, "game.iso")
        xiso.build_image(cls.files, cls.iso)
        cls.iso_bytes = open(cls.iso, "rb").read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fresh_dir(self):
        d = tempfile.mkdtemp(prefix="god2iso_case_", dir=self.tmp)
        return d

    # -- unit-ish tests ------------------------------------------------------

    def test_build_and_list(self):
        listing = xiso.list_image(self.iso)
        names = {rel for rel, _d, _s in listing}
        for f in self.files:
            self.assertIn(f, names)
        self.assertTrue(xiso.find_default_xex(self.iso))
        # sizes match
        by_name = {rel: size for rel, _d, size in listing}
        for f, blob in self.files.items():
            self.assertEqual(by_name[f], len(blob))

    def test_empty_table(self):
        t = xiso._encode_table([])
        self.assertEqual(len(t), SECTOR)
        self.assertEqual(t, bytes([0xFF]) * SECTOR)
        self.assertEqual(xiso.walk_table(t), [])
        self.assertEqual(xiso._table_size([]), SECTOR)

    def test_interleave_math(self):
        payload = bytes(0xCC000 * 3 + 0x1234)
        part = make_fake_god._build_part(payload)
        # part = master(0x1000) + N x [sub(0x1000) + data(0xCC000)]
        #       with a partial final period: + [sub(0x1000) + data(0x1234)]
        self.assertEqual(len(part), 0x5000 + len(payload))
        # the first 0x2000 bytes are hash material, not data
        self.assertEqual(part[0x1000:0x2000],
                         make_fake_god._sub_list(payload[0:0xCC000]))
        # de-interleaving with the converter's exact loop restores payload
        out = bytearray()
        pos = 0x2000
        while pos < len(part):
            out += part[pos:pos + 0xCC000]
            pos += 0xCC000 + 0x1000
        self.assertEqual(bytes(out), payload)

    def test_live_parser(self):
        live = make_fake_god.make_live(title_id=0x12345678, media_id=0x9ABCDEF0,
                                       part_count=2, combined_size=0x123000)
        info = xcontent.parse(live)
        self.assertEqual(info.magic, b"LIVE")
        self.assertEqual(info.title_id, 0x12345678)
        self.assertEqual(info.media_id, 0x9ABCDEF0)
        self.assertEqual(info.content_type, xcontent.CONTENT_TYPE_GOD)
        self.assertEqual(xcontent.pick_part_count(info, 2), 2)
        self.assertEqual(xcontent.pick_combined_size(info, 0x200000), 0x123000)

    # -- flavor A: embedded XSF header ---------------------------------------

    def test_flavor_a_byte_exact(self):
        d = self.fresh_dir()
        live, parts = make_fake_god.godify(
            self.iso_bytes, d, flavor="A",
            part_cuts=[0xCC000, 3 * 0xCC000])    # period-aligned, 3 parts
        self.assertEqual(len(parts), 3)
        rc = run_tool("convert", live, "-o", os.path.join(d, "out.iso"))
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        out = open(os.path.join(d, "out.iso"), "rb").read()
        # byte-exact: the de-interleaved stream == payload == XSF + ISO[0x10000:]
        expected = bytes(xsf.make_xsf_header(len(self.iso_bytes))) \
            + self.iso_bytes[xsf.XSF_SIZE:]
        self.assertEqual(out, expected)
        self.assertIn("default.xex: FOUND", rc.stdout)

    # -- flavor B: synthesized XSF header -------------------------------------

    def test_flavor_b_byte_exact(self):
        d = self.fresh_dir()
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="B",
                                       vd_flags=0x00, vd_offset_raw=0)
        rc = run_tool("convert", live, "-o", os.path.join(d, "out.iso"))
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        out = open(os.path.join(d, "out.iso"), "rb").read()
        expected = bytes(xsf.make_xsf_header()) + self.iso_bytes[xsf.XSF_SIZE:]
        # converter patches the synthesized header with the final length
        expected = bytearray(expected)
        xsf.patch_xsf_header(expected, len(out))
        self.assertEqual(out, bytes(expected))
        self.assertIn("default.xex: FOUND", rc.stdout)

    # -- flavor B + sector-offset fixing --------------------------------------

    def test_flavor_b_sector_offset_fix(self):
        d = self.fresh_dir()
        inflated = inflate_table_fields(self.iso_bytes, 4)
        live, _ = make_fake_god.godify(inflated, d, flavor="B",
                                       vd_flags=0x40, vd_offset_raw=19)  # -4
        rc = run_tool("convert", live, "-o", os.path.join(d, "out.iso"))
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertIn("corrected", rc.stdout)
        out = os.path.join(d, "out.iso")
        # root table field must be back to the original value
        with open(out, "rb") as f:
            f.seek(xiso.VOLUME_DESCRIPTOR_OFFSET + 0x14)
            root = struct.unpack("<I", f.read(4))[0]
        self.assertEqual(root, xiso.ROOT_DEFAULT_SECTOR)
        # and the image must parse with an identical file listing
        listing = xiso.list_image(out)
        by_name = {rel: size for rel, _d, size in listing}
        for f, blob in self.files.items():
            self.assertEqual(by_name[f], len(blob), f)
        self.assertTrue(xiso.find_default_xex(out))

    # -- multi-part, padding, trim --------------------------------------------

    def test_multi_part_padding_and_trim(self):
        d = self.fresh_dir()
        cuts = [0xCC000, 3 * 0xCC000]
        live, parts = make_fake_god.godify(self.iso_bytes, d, flavor="A",
                                           part_cuts=cuts, pad_last=0x40000)
        self.assertEqual(len(parts), 3)
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        raw = open(out, "rb").read()
        expected = bytes(xsf.make_xsf_header(len(self.iso_bytes))) \
            + self.iso_bytes[xsf.XSF_SIZE:] + bytes(0x40000)
        self.assertEqual(raw, expected)

        out2 = os.path.join(d, "out.trim.iso")
        rc = run_tool("convert", live, "-o", out2, "--trim")
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        trimmed = open(out2, "rb").read()
        self.assertEqual(trimmed, expected[:-0x40000])

    # -- alternate on-disk layout (00007000\\00000001 style) ------------------

    def test_alt_layout(self):
        d = self.fresh_dir()
        god_root = os.path.join(d, "Content", "0000000000000000", "TEST0001")
        meta_dir = os.path.join(god_root, "0000000000000000")
        data_dir = os.path.join(god_root, "00007000")
        os.makedirs(meta_dir)
        os.makedirs(data_dir)
        live, parts = make_fake_god.godify(
            self.iso_bytes, self.fresh_dir(), flavor="A",
            part_cuts=[0xCC000])
        shutil.copy(live, os.path.join(meta_dir, "TEST0001.live"))
        for i, p in enumerate(parts):
            shutil.copy(p, os.path.join(data_dir, "%08d" % (i + 1)))
        rc = run_tool("convert", os.path.join(meta_dir, "TEST0001.live"),
                      "-o", os.path.join(d, "out.iso"))
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        out = open(os.path.join(d, "out.iso"), "rb").read()
        expected = bytes(xsf.make_xsf_header(len(self.iso_bytes))) \
            + self.iso_bytes[xsf.XSF_SIZE:]
        self.assertEqual(out, expected)

    # -- classic iso2god naming (header without .live extension) ---------------

    def test_classic_iso2god_naming(self):
        """The classic iso2god / on-console layout names the header by Media
        ID with no extension (<MediaID> + <MediaID>.data).  The tool must
        auto-detect it and convert it byte-identically."""
        import god2iso
        d = self.fresh_dir()
        media = "082DACEE274BCE0F6ED4"          # 16-char media-id style name
        live, parts = make_fake_god.godify(
            self.iso_bytes, d, flavor="A", live_name=media)
        self.assertEqual(os.path.basename(live), media)       # no extension
        self.assertTrue(os.path.isdir(live + ".data"))
        # auto-detection from the containing folder
        found = god2iso.find_live_files(d)
        self.assertIn(live, found)
        self.assertNotIn(parts[0], found)        # Data0000 must NOT be picked
        # direct file path also accepted
        self.assertEqual(god2iso.find_live_files(live), [live])
        # conversion is byte-identical to the .live-named layout
        out = os.path.join(d, "classic.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        expected = bytes(xsf.make_xsf_header(len(self.iso_bytes))) \
            + self.iso_bytes[xsf.XSF_SIZE:]
        self.assertEqual(open(out, "rb").read(), expected)
        self.assertIn("default.xex: FOUND", rc.stdout)

    # -- Redump-style payload (game partition at an offset) --------------------

    def test_redump_style_partition_offset(self):
        """GOD built from a full XGD2/XGD3 image carries the game partition
        starting at an offset (magic at partition_start + 0x10000).  The
        converter must detect the offset instead of wrongly reporting the
        package as encrypted, and verification must pass."""
        d = self.fresh_dir()
        payload = b"\x00" * 0x10000 + self.iso_bytes[0x10000:]
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="B",
                                       raw_payload=payload)
        out = os.path.join(d, "redump.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        # marker located after synthesized XSF + 0x10000 partition head
        self.assertEqual(xiso.find_xdvdfs_offset(out), 0x20000)
        self.assertIn("default.xex: FOUND", rc.stdout)
        self.assertIn("partition at offset", rc.stdout)
        # auto-detection on list
        rc2 = run_tool("list", out)
        self.assertEqual(rc2.returncode, 0, rc2.stderr)
        self.assertIn("default.xex", rc2.stdout)
        # byte-exact: output[0x10000:] must equal the stored payload
        out_bytes = open(out, "rb").read()
        self.assertEqual(out_bytes[0x10000:], payload)
        # extract via auto-detected offset
        exdir = os.path.join(d, "x")
        rc3 = run_tool("extract", out, exdir)
        self.assertEqual(rc3.returncode, 0, rc3.stderr)
        for rel, blob in self.files.items():
            with open(os.path.join(exdir, rel), "rb") as f:
                self.assertEqual(f.read(), blob, rel)
        # rebuild from the offset image
        rb = os.path.join(d, "rb.iso")
        rc4 = run_tool("rebuild", out, "-o", rb)
        self.assertEqual(rc4.returncode, 0, rc4.stderr)
        self.assertEqual(xiso.find_default_xex(rb), "default.xex")

    # -- Merkle hash tree (MHT) deep verification ------------------------------

    def test_mht_verify_passes_multipart(self):
        """convert must deep-verify and pass on multi-part packages (both
        flavors), proving the extracted data matches the stored SHA-1 tree."""
        for flavor in ("A", "B"):
            with self.subTest(flavor=flavor):
                d = self.fresh_dir()
                live, _ = make_fake_god.godify(
                    self.iso_bytes, d, flavor=flavor,
                    part_cuts=[0xCC000, 3 * 0xCC000])
                out = os.path.join(d, "out.iso")
                rc = run_tool("convert", live, "-o", out)
                self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
                self.assertIn("MHT verification: PASSED", rc.stdout)
                self.assertNotIn("FAILED", rc.stdout)

    def test_mht_verify_detects_corrupted_data(self):
        """A single flipped byte in a part's data must be caught by the
        deep verification (exit code 4), even though the ISO still parses."""
        d = self.fresh_dir()
        live, parts = make_fake_god.godify(self.iso_bytes, d, flavor="A",
                                           part_cuts=[0xCC000])
        # flip a byte inside data0 of part0 (offset 0x2000 + 0x30000);
        # stream offset 0x30000 lies in the ISO's unused header region, so
        # the filesystem still parses - only the MHT check can catch it
        with open(parts[0], "r+b") as f:
            f.seek(0x2000 + 0x30000)
            b = f.read(1)
            f.seek(0x2000 + 0x30000)
            f.write(bytes([b[0] ^ 0xFF]))
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 4, rc.stdout + rc.stderr)
        self.assertIn("MHT verification: FAILED", rc.stdout)
        # with --no-verify the conversion still completes (rc 0: ISO parses)
        out2 = os.path.join(d, "out2.iso")
        rc2 = run_tool("convert", live, "-o", out2, "--no-verify")
        self.assertEqual(rc2.returncode, 0, rc2.stdout + rc2.stderr)
        self.assertNotIn("MHT verification", rc2.stdout)

    def test_mht_verify_zeroed_hashes_tolerated(self):
        """GODs whose hash lists were not populated (all zeroes) must still
        convert; deep verification reports it as skipped, not failed."""
        d = self.fresh_dir()
        live, parts = make_fake_god.godify(self.iso_bytes, d, flavor="A",
                                           part_cuts=[0xCC000])
        # zero the master + first sub-list
        with open(parts[0], "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 0x2000)
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertIn("zeroed", rc.stdout)
        self.assertNotIn("FAILED", rc.stdout)

    def test_mht_verify_detects_corrupted_master(self):
        """Tampering with a stored master-list hash must fail verification."""
        d = self.fresh_dir()
        live, parts = make_fake_god.godify(self.iso_bytes, d, flavor="A",
                                           part_cuts=[0xCC000])
        with open(parts[0], "r+b") as f:
            f.seek(0x10)
            b = f.read(1)
            f.seek(0x10)
            f.write(bytes([b[0] ^ 0xFF]))
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 4, rc.stdout + rc.stderr)
        self.assertIn("MHT verification: FAILED", rc.stdout)

    def test_mht_verify_detects_wrong_root(self):
        """A .live whose MHT root hash does not match the parts must fail."""
        d = self.fresh_dir()
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="A",
                                       part_cuts=[0xCC000])
        # corrupt one byte of the root hash in the .live
        with open(live, "r+b") as f:
            f.seek(0x37D)
            b = f.read(1)
            f.seek(0x37D)
            f.write(bytes([b[0] ^ 0xFF]))
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 4, rc.stdout + rc.stderr)
        self.assertIn("root", rc.stdout)

    # -- info / list / extract / rebuild --------------------------------------

    def test_info_command(self):
        d = self.fresh_dir()
        live, parts = make_fake_god.godify(self.iso_bytes, d, flavor="A")
        rc = run_tool("info", live)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertIn("title id        : 54455354", rc.stdout)
        self.assertIn("part files      : 1", rc.stdout)

    def test_extract(self):
        d = self.fresh_dir()
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="A")
        out_iso = os.path.join(d, "out.iso")
        run_tool("convert", live, "-o", out_iso)
        outdir = os.path.join(d, "x")
        rc = run_tool("extract", out_iso, outdir)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        for rel, blob in self.files.items():
            with open(os.path.join(outdir, rel), "rb") as f:
                self.assertEqual(f.read(), blob, rel)

    def test_rebuild(self):
        d = self.fresh_dir()
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="B")
        out_iso = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out_iso)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        rebuilt = os.path.join(d, "rebuilt.iso")
        rc = run_tool("rebuild", out_iso, "-o", rebuilt)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertIn("default.xex: FOUND", rc.stdout)
        listing = xiso.list_image(rebuilt)
        by_name = {rel: size for rel, _d, size in listing}
        for f, blob in self.files.items():
            self.assertEqual(by_name[f], len(blob), f)
        # contents survive the rebuild
        with open(rebuilt, "rb") as f:
            root_sector, root_size, _ = xiso.read_volume_descriptor(f)
            for e in xiso.read_table(f, root_sector, root_size):
                if e.name == "default.xex":
                    self.assertEqual(xiso.read_file(f, e.sector, e.size),
                                     self.files["default.xex"])

    # -- encrypted detection ---------------------------------------------------

    def test_encrypted_detection(self):
        d = self.fresh_dir()
        import random
        rng = random.Random(42)
        junk = bytes(rng.randrange(256) for _ in range(0x30000))
        live, _ = make_fake_god.godify(junk, d, flavor="A")
        out_iso = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out_iso)
        self.assertEqual(rc.returncode, 3)          # verify failed
        self.assertIn("ENCRYPTED", rc.stdout)

    def test_live_parser_rejects_garbage(self):
        d = self.fresh_dir()
        bad = os.path.join(d, "bad.live")
        open(bad, "wb").write(b"this is not a live file" * 100)
        rc = run_tool("info", bad)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("not an XContent package", rc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
