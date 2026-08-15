#!/usr/bin/env python3
"""Cross-validation against the REFERENCE implementation.

Builds the actual extract-xiso v2.7.1 (XboxDev) from source and uses it as
an independent oracle:

  * my XDVDFS writer  -> reference reader (extract) must yield identical files
  * reference writer  -> my XDVDFS reader must yield identical files
  * my GOD->ISO pipeline (both XSF flavors, multi-part) -> reference reader

The whole test file is skipped automatically when the reference binary is
not available (set EXTRACT_XISO=/path/to/extract-xiso to point at it).

Run:  python3 tests/test_cross_tool.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xiso            # noqa: E402
import make_fake_god   # noqa: E402

EXTRACT_XISO = os.environ.get("EXTRACT_XISO") or shutil.which("extract-xiso")
if not EXTRACT_XISO:
    for cand in ("/home/user/research/extract-xiso/extract-xiso-bin",):
        if os.path.exists(cand):
            EXTRACT_XISO = cand


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root):
    """{(relpath): sha256} for every regular file under root."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = sha256_file(full)
    return out


def xiso_extract(iso, outdir):
    return subprocess.run([EXTRACT_XISO, "-q", "-x", iso, "-d", outdir],
                          capture_output=True, text=True)


def xiso_create(folder, out_iso):
    return subprocess.run([EXTRACT_XISO, "-q", "-c", folder, out_iso],
                          capture_output=True, text=True)


def make_big_tree():
    """~350 files incl. >204 in one directory (forces real tree offsets),
    nested dirs, deeper chains."""
    files = {}
    files["default.xex"] = bytes(range(256)) * 4096          # 1 MB
    files["default_mp.xex"] = os.urandom(256 * 1024)
    files["readme.txt"] = b"cross-tool validation\n" * 500
    for i in range(250):                                     # > 204 entries
        files["media/tex_%04d.bin" % i] = os.urandom((i % 37 + 1) * 256)
    files["media/shaders/vert.hlsl"] = b"vertex" * 3000
    files["media/shaders/frag.hlsl"] = b"fragment" * 3000
    files["media/movies/intro.bik"] = os.urandom(4 * 1024 * 1024)
    files["data/levels/level01/map.bin"] = os.urandom(300 * 1024)
    files["data/levels/level01/nav.bin"] = b"nav" * 4096
    files["data/levels/level02/map.bin"] = os.urandom(100 * 1024)
    files["system/update/upd.bin"] = b"update" * 512
    files["misc/empty_file.dat"] = b""
    return files


@unittest.skipUnless(EXTRACT_XISO, "extract-xiso reference binary not available")
class CrossToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cross_")
        cls.files = make_big_tree()
        # also create the reference-side source folder (with an EMPTY dir,
        # which only the reference writer can produce)
        cls.src_dir = os.path.join(cls.tmp, "srcdir")
        for rel, blob in cls.files.items():
            p = os.path.join(cls.src_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(blob)
        os.makedirs(os.path.join(cls.src_dir, "emptydir"))
        # ISO built by MY writer, shared by the pipeline tests
        cls.mine_iso = os.path.join(cls.tmp, "mine.iso")
        xiso.build_image(cls.files, cls.mine_iso)
        cls.iso_bytes = open(cls.mine_iso, "rb").read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_my_writer_reference_reader(self):
        """ISO built by my writer must extract identically with extract-xiso."""
        iso = self.mine_iso
        outdir = os.path.join(self.tmp, "x_mine")
        rc = xiso_extract(iso, outdir)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertEqual(tree_hashes(outdir), tree_hashes(self.src_dir))

    def test_reference_writer_my_reader(self):
        """ISO built by extract-xiso must parse/extract identically with my reader."""
        iso = os.path.join(self.tmp, "ref.iso")
        rc = xiso_create(self.src_dir, iso)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        outdir = os.path.join(self.tmp, "x_ref")
        xiso.extract_image(iso, outdir)
        self.assertEqual(tree_hashes(outdir), tree_hashes(self.src_dir))
        # and my listing must agree on the file count
        listing = xiso.list_image(iso)
        n_files = sum(1 for _, d, _ in listing if not d)
        self.assertEqual(n_files, len(self.files))
        self.assertEqual(xiso.find_default_xex(iso), "default.xex")

    def test_god_flavorA_pipeline_reference_reader(self):
        """GOD (flavor A, embedded XSF, 2 parts) -> my convert ->
        reference extractor must produce the original files."""
        d = os.path.join(self.tmp, "godA")
        os.makedirs(d)
        live, parts = make_fake_god.godify(self.iso_bytes, d, flavor="A",
                                           part_cuts=[0xCC000, 3 * 0xCC000])
        self.assertGreater(len(parts), 1)
        out_iso = os.path.join(self.tmp, "outA.iso")
        import god2iso
        rc = god2iso.convert(live, out_iso, quiet=True)
        self.assertEqual(rc, 0)
        outdir = os.path.join(self.tmp, "x_A")
        rc = xiso_extract(out_iso, outdir)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertEqual(tree_hashes(outdir), tree_hashes(self.src_dir))

    def test_god_flavorB_pipeline_reference_reader(self):
        """GOD (flavor B, synthesized XSF) -> my convert ->
        reference extractor must produce the original files."""
        d = os.path.join(self.tmp, "godB")
        os.makedirs(d)
        live, _ = make_fake_god.godify(self.iso_bytes, d, flavor="B")
        out_iso = os.path.join(self.tmp, "outB.iso")
        import god2iso
        rc = god2iso.convert(live, out_iso, quiet=True)
        self.assertEqual(rc, 0)
        outdir = os.path.join(self.tmp, "x_B")
        rc = xiso_extract(out_iso, outdir)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        self.assertEqual(tree_hashes(outdir), tree_hashes(self.src_dir))

    def test_reference_rebuild_then_my_reader(self):
        """extract-xiso's own rewrite (-r) output must also read back fine."""
        iso = os.path.join(self.tmp, "ref.iso")
        rc = xiso_create(self.src_dir, iso)
        self.assertEqual(rc.returncode, 0)
        # copy to a new name for -r (it rewrites in place via .old)
        iso2 = os.path.join(self.tmp, "ref2.iso")
        shutil.copy(iso, iso2)
        rc = subprocess.run([EXTRACT_XISO, "-q", "-r", iso2],
                            capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
        outdir = os.path.join(self.tmp, "x_ref2")
        xiso.extract_image(iso2, outdir)
        self.assertEqual(tree_hashes(outdir), tree_hashes(self.src_dir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
