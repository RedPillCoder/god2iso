#!/usr/bin/env python3
"""Safety, security, privacy and portability tests for god2iso.py.

Run:  python3 tests/test_safety.py
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import util            # noqa: E402
import xiso            # noqa: E402
import make_fake_god   # noqa: E402
from test_roundtrip import make_file_map, run_tool  # noqa: E402

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(TOOL_DIR, "god2iso.py")

SECTOR = 0x800


def make_iso_bytes():
    d = tempfile.mkdtemp(prefix="safety_iso_")
    iso = os.path.join(d, "game.iso")
    xiso.build_image(make_file_map(), iso)
    return d, open(iso, "rb").read()


def make_god(iso_bytes, tmp, flavor="A", **kw):
    god = os.path.join(tmp, "god")
    os.makedirs(god, exist_ok=True)
    return make_fake_god.godify(iso_bytes, god, flavor=flavor, **kw)


class SafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="god2iso_safety_")
        cls.iso_dir, cls.iso_bytes = make_iso_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fresh(self):
        return tempfile.mkdtemp(prefix="case_", dir=self.tmp)

    # ------------------------------------------------------------- privacy

    def test_no_network_imports_in_source(self):
        problems = util.audit_no_network(TOOL_DIR)
        self.assertEqual(problems, [],
                         "network-capable imports found: %r" % problems)

    def test_audit_command_clean(self):
        rc = run_tool("audit")
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertIn("fully offline", rc.stdout)

    # ------------------------------------------------- non-destructive

    def test_source_tree_untouched(self):
        d = self.fresh()
        live, parts = make_god(self.iso_bytes, d, flavor="A")
        src_root = os.path.dirname(live)
        before = util.tree_manifest(src_root)
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        after = util.tree_manifest(src_root)
        self.assertEqual(before, after,
                         "conversion modified the GOD source tree!")

    def test_refuses_existing_output(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out = os.path.join(d, "out.iso")
        open(out, "wb").write(b"precious data")
        rc = run_tool("convert", live, "-o", out)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("refusing to overwrite", rc.stderr)
        self.assertEqual(open(out, "rb").read(), b"precious data",
                         "existing output was modified without --force")

    def test_force_overwrites(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out = os.path.join(d, "out.iso")
        open(out, "wb").write(b"old")
        rc = run_tool("convert", live, "-o", out, "--force")
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertNotEqual(open(out, "rb").read(), b"old")

    def test_refuses_overwriting_input_file(self):
        d = self.fresh()
        live, parts = make_god(self.iso_bytes, d, flavor="A")
        src = os.path.dirname(live)
        before = util.tree_manifest(src)
        # output colliding with the .live file
        rc = run_tool("convert", live, "-o", live)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("refusing to overwrite a GOD input file", rc.stderr)
        # output colliding with a part file
        rc = run_tool("convert", live, "-o", parts[0])
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("refusing to overwrite a GOD input file", rc.stderr)
        # input files untouched by the refused attempts
        self.assertEqual(util.tree_manifest(src), before)

    def test_failed_convert_leaves_no_residue(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        # sabotage: make the output path a DIRECTORY and force the attempt,
        # so the final atomic rename must fail -> temp file gets cleaned up
        out = os.path.join(d, "out.iso")
        os.mkdir(out)
        rc = run_tool("convert", live, "-o", out, "--force")
        self.assertNotEqual(rc.returncode, 0)
        leftovers = [n for n in os.listdir(d) if n.startswith(".god2iso-tmp-")]
        self.assertEqual(leftovers, [], "temp files left behind: %r"
                         % leftovers)
        self.assertTrue(os.path.isdir(out), "destination was clobbered")

    def test_truncated_part_is_detected(self):
        d = self.fresh()
        live, parts = make_god(self.iso_bytes, d, flavor="A")
        # truncate the last part to almost nothing
        with open(parts[-1], "r+b") as f:
            f.truncate(0x1000)
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertNotEqual(rc.returncode, 0)
        leftovers = [n for n in os.listdir(d) if n.startswith(".god2iso-tmp-")]
        self.assertEqual(leftovers, [])
        self.assertFalse(os.path.exists(out))

    def test_convert_idempotent(self):
        d1, d2 = self.fresh(), self.fresh()
        live1, _ = make_god(self.iso_bytes, d1, flavor="B")
        # replicate the same GOD tree in a second directory
        shutil.copytree(os.path.join(d1, "god"), os.path.join(d2, "god"))
        live2 = os.path.join(d2, "god", os.path.basename(live1))
        o1 = os.path.join(d1, "a.iso")
        o2 = os.path.join(d2, "b.iso")
        self.assertEqual(run_tool("convert", live1, "-o", o1).returncode, 0)
        self.assertEqual(run_tool("convert", live2, "-o", o2).returncode, 0)
        self.assertEqual(open(o1, "rb").read(), open(o2, "rb").read(),
                         "conversion is not deterministic")

    # ------------------------------------------------------------ security

    def test_traversal_names_rejected(self):
        d = self.fresh()
        # build an ISO then patch a same-length filename to "../evil"
        files = {"default.xex": b"X" * 4096, "evilfile": b"E" * 16}
        iso = os.path.join(d, "t.iso")
        xiso.build_image(files, iso)
        data = bytearray(open(iso, "rb").read())
        evil = b"../evil"
        idx = data.find(b"evilfile")
        self.assertGreater(idx, 0)
        data[idx:idx + len(evil)] = evil
        bad = os.path.join(d, "bad.iso")
        open(bad, "wb").write(bytes(data))
        rc = run_tool("list", bad)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("unsafe filename", rc.stderr)
        outdir = os.path.join(d, "x")
        rc = run_tool("extract", bad, outdir)
        self.assertNotEqual(rc.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(d, "evil")))

    def test_backslash_and_control_names_rejected(self):
        # names that are rejected even when embedded in a longer string
        for badname in ["a\\b", "a\x00b", "a\x01b"]:
            with self.subTest(name=badname):
                d = self.fresh()
                files = {"default.xex": b"X" * 4096}
                iso = os.path.join(d, "t.iso")
                xiso.build_image(files, iso)
                data = bytearray(open(iso, "rb").read())
                idx = data.find(b"default.xex")
                self.assertGreater(idx, 0)
                data[idx:idx + 11] = badname.encode("latin-1").ljust(11, b"_")
                bad = os.path.join(d, "bad.iso")
                open(bad, "wb").write(bytes(data))
                rc = run_tool("list", bad)
                self.assertNotEqual(rc.returncode, 0)
                self.assertIn("unsafe filename", rc.stderr)
        # exact-name checks (pure unit level)
        for badname in ["..", ".", "CON", "NUL", "a.txt.", "a.txt "]:
            with self.subTest(unit=badname):
                with self.assertRaises(xiso.XisoError):
                    xiso._check_name(badname)

    def test_extract_symlink_escape_refused(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out_iso = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out_iso)
        self.assertEqual(rc.returncode, 0)
        outdir = os.path.join(d, "x")
        os.makedirs(outdir)
        # plant a symlink where the extractor would create "media"
        os.symlink(d, os.path.join(outdir, "media"))
        rc = run_tool("extract", out_iso, outdir)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("symlink", rc.stderr)
        # nothing was written through the symlink
        self.assertFalse(os.path.exists(os.path.join(d, "default.xex")))

    def test_extract_case_collision_refused(self):
        d = self.fresh()
        files = {"default.xex": b"X" * 4096, "readme.txt": b"a",
                 "README.TXT": b"b"}
        iso = os.path.join(d, "c.iso")
        xiso.build_image(files, iso)
        outdir = os.path.join(d, "x")
        rc = run_tool("extract", iso, outdir)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("case-insensitive collision", rc.stderr)

    def test_extract_refuses_overwrite_without_force(self):
        d = self.fresh()
        files = {"default.xex": b"X" * 4096, "readme.txt": b"a"}
        iso = os.path.join(d, "c.iso")
        xiso.build_image(files, iso)
        outdir = os.path.join(d, "x")
        os.makedirs(outdir)
        open(os.path.join(outdir, "readme.txt"), "wb").write(b"keep me")
        rc = run_tool("extract", iso, outdir)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("refusing to overwrite", rc.stderr)
        self.assertEqual(open(os.path.join(outdir, "readme.txt"), "rb").read(),
                         b"keep me")
        # with --force only the extracted file changes; others untouched
        rc = run_tool("extract", iso, outdir, "--force")
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertEqual(open(os.path.join(outdir, "readme.txt"), "rb").read(),
                         b"a")
        self.assertEqual(open(os.path.join(outdir, "default.xex"), "rb").read(),
                         b"X" * 4096)

    def test_rebuild_refuses_existing_output(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out_iso = os.path.join(d, "out.iso")
        self.assertEqual(run_tool("convert", live, "-o", out_iso).returncode, 0)
        rebuilt = os.path.join(d, "r.iso")
        open(rebuilt, "wb").write(b"keep")
        rc = run_tool("rebuild", out_iso, "-o", rebuilt)
        self.assertNotEqual(rc.returncode, 0)
        self.assertEqual(open(rebuilt, "rb").read(), b"keep")

    # -------------------------------------------------- usability

    def test_wizard_with_piped_input(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out = os.path.join(d, "wiz.iso")
        feed = "%s\n%s\ny\n" % (live, out)
        rc = subprocess.run([sys.executable, TOOL, "--wizard"],
                            input=feed, capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(xiso.find_default_xex(out), "default.xex")

    def test_wizard_quit_on_enter(self):
        rc = subprocess.run([sys.executable, TOOL, "--wizard"],
                            input="\n", capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0)

    def test_sha256_flag(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out, "--sha256")
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        expected = hashlib.sha256(open(out, "rb").read()).hexdigest()
        self.assertIn(expected, rc.stdout)

    def test_progress_flag_runs(self):
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out, "--progress")
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)

    def test_encrypted_warning_and_exit_code(self):
        import random
        rng = random.Random(7)
        junk = bytes(rng.randrange(256) for _ in range(0x30000))
        d = self.fresh()
        live, _ = make_god(junk, d, flavor="A")
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 3)
        self.assertIn("ENCRYPTED", rc.stdout)
        self.assertIn("no title keys", rc.stdout)

    def test_help_and_version(self):
        rc = run_tool("--version")
        self.assertEqual(rc.returncode, 0)
        rc = run_tool("--help")
        self.assertEqual(rc.returncode, 0)
        self.assertIn("offline", rc.stdout)

    # ------------------------------------------------ clean error handling

    def test_clean_error_nonexistent_outdir(self):
        """A bad output path must give a clean message, never a traceback."""
        d = self.fresh()
        live, _ = make_god(self.iso_bytes, d, flavor="A")
        rc = run_tool("convert", live, "-o", "/nonexistent-dir-xyz/out.iso")
        self.assertEqual(rc.returncode, 1)
        self.assertIn("output directory does not exist", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_clean_error_multiple_lives(self):
        """Multiple packages with no -o must give a clean, actionable message."""
        d = self.fresh()
        g1 = os.path.join(d, "god1"); os.makedirs(g1)
        g2 = os.path.join(d, "god2"); os.makedirs(g2)
        make_fake_god.godify(self.iso_bytes, g1, flavor="A",
                             live_name="GAME0001.live")
        make_fake_god.godify(self.iso_bytes, g2, flavor="B",
                             live_name="GAME0002.live")
        rc = run_tool("convert", d)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("multiple .live files", rc.stdout + rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_clean_error_bad_live_file(self):
        d = self.fresh()
        bad = os.path.join(d, "bad.live")
        open(bad, "wb").write(b"\x00" * 64)
        rc = run_tool("info", bad)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("XContent package", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_clean_error_missing_live(self):
        rc = run_tool("convert", "/nonexistent-folder-xyz")
        self.assertEqual(rc.returncode, 1)
        self.assertIn("no .live file found", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_clean_error_bad_live_on_convert(self):
        """An unparseable .live passed to convert must be a clean error."""
        d = self.fresh()
        bad = os.path.join(d, "bad.live")
        open(bad, "wb").write(b"\x00" * 64)
        rc = run_tool("convert", bad, "-o", os.path.join(d, "out.iso"))
        self.assertEqual(rc.returncode, 1)
        self.assertIn("XContent package", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)
        self.assertFalse(os.path.exists(os.path.join(d, "out.iso")))

    # ------------------------------------------------ resource hardening

    def test_oversized_directory_table_rejected(self):
        """A directory table claiming > 64 MB must be refused, not allocated."""
        d = self.fresh()
        files = {"default.xex": b"X" * 4096}
        iso = os.path.join(d, "t.iso")
        xiso.build_image(files, iso)
        data = bytearray(open(iso, "rb").read())
        # inflate the root table size field in the volume descriptor
        struct.pack_into("<I", data, xiso.VOLUME_DESCRIPTOR_OFFSET + 0x18,
                         0x10000000)
        bad = os.path.join(d, "bad.iso")
        open(bad, "wb").write(bytes(data))
        rc = run_tool("list", bad)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("absurdly large", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_deep_directory_nesting_rejected(self):
        """More than MAX_DIR_DEPTH nested dirs must be refused cleanly."""
        d = self.fresh()
        deep = "/".join(["d%02d" % i for i in range(140)]) + "/f.txt"
        files = {deep: b"x"}
        iso = os.path.join(d, "t.iso")
        xiso.build_image(files, iso)
        rc = run_tool("list", iso)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("nesting too deep", rc.stderr)
        self.assertNotIn("Traceback", rc.stderr)

    def test_non_ascii_filename_does_not_crash(self):
        """Latin-1 filenames from a crafted table must list without crashing
        on any console encoding (errors='replace')."""
        d = self.fresh()
        files = {"default.xex": b"X" * 4096, "readme.txt": b"a"}
        iso = os.path.join(d, "t.iso")
        xiso.build_image(files, iso)
        data = bytearray(open(iso, "rb").read())
        idx = data.find(b"readme.txt")
        data[idx] = 0xE9                       # 'readme.txt' -> 'r\xe9adme.txt'
        bad = os.path.join(d, "u.iso")
        open(bad, "wb").write(bytes(data))
        rc = run_tool("list", bad)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertIn("\xe9eadme.txt", rc.stdout)

    def test_build_image_streams_from_paths(self):
        """build_image must accept file paths and produce the same ISO as
        raw bytes (streaming path is memory-bounded)."""
        d = self.fresh()
        files = {"default.xex": b"X" * 8192, "media/a.bin": os.urandom(65536)}
        iso_bytes = os.path.join(d, "b.iso")
        xiso.build_image(files, iso_bytes)
        # now write the same content to disk and build from paths
        src = os.path.join(d, "src")
        for rel, blob in files.items():
            p = os.path.join(src, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(blob)
        path_map = {rel: os.path.join(src, *rel.split("/")) for rel in files}
        iso_paths = os.path.join(d, "p.iso")
        xiso.build_image(path_map, iso_paths)
        a = bytearray(open(iso_bytes, "rb").read())
        b = bytearray(open(iso_paths, "rb").read())
        # the volume-descriptor FILETIME differs between builds; zero it
        off = xiso.VOLUME_DESCRIPTOR_OFFSET + 0x1C
        a[off:off + 8] = b"\x00" * 8
        b[off:off + 8] = b"\x00" * 8
        self.assertEqual(bytes(a), bytes(b))

    def test_rebuild_large_file_memory_bounded(self):
        """Rebuild must handle a large file via streaming (path-based)."""
        d = self.fresh()
        big = os.urandom(24 * 1024 * 1024)      # 24 MB
        files = {"default.xex": b"X" * 4096, "big.bin": big}
        iso = os.path.join(d, "t.iso")
        xiso.build_image(files, iso)
        out = os.path.join(d, "r.iso")
        rc = run_tool("rebuild", iso, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        # verify the big file survived byte-identically
        with open(out, "rb") as f:
            rs, rsz, _ = xiso.read_volume_descriptor(f)
            for e in xiso.read_table(f, rs, rsz):
                if e.name == "big.bin":
                    self.assertEqual(xiso.read_file(f, e.sector, e.size), big)

    def test_atomic_output_falls_back_to_system_temp(self):
        """When the destination directory is not writable, atomic_output
        must fall back to the system temp dir instead of failing."""
        import tempfile as _tf
        from unittest import mock
        d = self.fresh()
        out = os.path.join(d, "out.iso")
        real_mkstemp = _tf.mkstemp

        def fake_mkstemp(*a, **kw):
            if kw.get("dir") == os.path.dirname(out):
                raise OSError("simulated unwritable destination")
            return real_mkstemp(*a, **kw)

        with mock.patch.object(_tf, "mkstemp", side_effect=fake_mkstemp):
            with util.atomic_output(out) as fh:
                fh.write(b"data")
        self.assertEqual(open(out, "rb").read(), b"data")

    def test_atomic_output_normal_path_still_works(self):
        d = self.fresh()
        out = os.path.join(d, "out.iso")
        with util.atomic_output(out) as fh:
            fh.write(b"data")
        self.assertEqual(open(out, "rb").read(), b"data")

    def test_negative_sector_offset_skipped(self):
        """A .live requesting a nonsense (<= 0) sector offset must not
        corrupt the output - the fix is skipped."""
        d = self.fresh()
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="B",
                                       vd_flags=0x40, vd_offset_raw=1)  # -32
        out = os.path.join(d, "out.iso")
        rc = run_tool("convert", live, "-o", out)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertEqual(xiso.find_default_xex(out), "default.xex")


if __name__ == "__main__":
    unittest.main(verbosity=2)
