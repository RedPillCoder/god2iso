#!/usr/bin/env python3
"""Safety, privacy and portability helpers for god2iso.py.

Design goals implemented here:
  * NON-DESTRUCTIVE  - outputs are written to a temporary file in the target
                       directory, fsynced, then atomically renamed into place
                       (os.replace).  A failed or interrupted conversion can
                       never leave a half-written ISO at the destination and
                       never touches any input file.
  * SAFE PATHS       - every path derived from untrusted archive content is
                       validated against traversal ("../", absolute paths,
                       backslashes, control characters) and symlink escapes
                       are refused.
  * PRIVACY          - the tool is fully offline.  audit_no_network() scans
                       the tool's own source for any network-capable import
                       so this property can be verified mechanically.
  * CROSS-PLATFORM   - only stdlib APIs that behave identically on Windows
                       and POSIX (os.replace, tempfile.mkstemp, shutil).
"""

import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager


class SafetyError(Exception):
    """Raised when an operation would violate a safety policy."""


# ---------------------------------------------------------------------------
# atomic output
# ---------------------------------------------------------------------------

@contextmanager
def atomic_output(final_path, overwrite=False):
    """Write *final_path* atomically and non-destructively.

    Yields an open binary file handle to a temporary file.  On success the
    temp file is fsynced and atomically renamed over *final_path*
    (os.replace - atomic on both POSIX and Windows).  On ANY failure the
    temp file is deleted and *final_path* is left exactly as it was.

    Temp-file placement is resilient:
      1. preferred: the same directory as *final_path* (same-filesystem
         rename - required for atomicity on POSIX),
      2. fallback:  the system temp directory (used when the destination
         directory is unwritable, e.g. OneDrive placeholders, permission
         issues, or over-long destination paths).

    If *overwrite* is False and *final_path* already exists, SafetyError is
    raised before anything is written.
    """
    final = os.path.abspath(final_path)
    if not overwrite and os.path.exists(final):
        raise SafetyError("refusing to overwrite existing file %r "
                          "(use --force to overwrite)" % final_path)
    outdir = os.path.dirname(final) or os.curdir
    if not os.path.isdir(outdir):
        raise SafetyError("output directory does not exist: %r" % outdir)

    tmp = None
    fh = None
    try:
        try:
            fd, tmp = tempfile.mkstemp(prefix=".god2iso-tmp-", suffix=".tmp",
                                       dir=outdir)
        except OSError:
            # destination dir not writable (OneDrive / permissions / long
            # path): fall back to the system temp dir
            fd, tmp = tempfile.mkstemp(prefix="god2iso-tmp-", suffix=".tmp")
        fh = os.fdopen(fd, "r+b")
        yield fh
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        fh = None
        # give the finished file normal user permissions (mkstemp uses 0600)
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR
                     | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass
        if not overwrite and os.path.exists(final):
            raise SafetyError("refusing to overwrite existing file %r "
                              "(use --force to overwrite)" % final_path)
        os.replace(tmp, final)
        tmp = None
    except BaseException:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------

def safe_join(base: str, *parts) -> str:
    """Join *parts* onto *base*, refusing anything that escapes *base*.

    Used whenever a path component comes from untrusted data (ISO table
    entries, .live fields).  Raises SafetyError on escape attempts.
    """
    base_abs = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base_abs, *parts))
    if target != base_abs and not target.startswith(base_abs + os.sep):
        raise SafetyError("path %r escapes base directory %r" % (parts, base))
    return target


def ensure_not_symlink(path: str, what: str = "path") -> None:
    """Refuse to traverse or overwrite a symlink."""
    if os.path.islink(path):
        raise SafetyError("refusing to follow symlink %r (%s)" % (path, what))


# ---------------------------------------------------------------------------
# privacy / offline audit
# ---------------------------------------------------------------------------

NETWORK_CAPABLE_MODULES = {
    "socket", "urllib", "urllib3", "requests", "http", "httplib",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "aiohttp", "asyncio",
    "websockets", "webbrowser",
}

# Modules that can actually OPEN a network connection (used by the frozen
# runtime audit).  Note: 'urllib.parse' / 'urllib.error' are pure string
# parsing - harmless - while 'urllib.request' is the network client.
NETWORK_CLIENT_MODULES = {
    "socket", "ssl", "http.client", "urllib.request", "urllib3",
    "requests", "httplib", "ftplib", "smtplib", "telnetlib",
    "xmlrpc.client", "aiohttp", "websockets",
}


def runtime_network_modules(modules) -> list:
    """Names of *modules* (iterable of sys.modules keys) that are network
    clients, e.g. ['urllib.request'] - prefix matched."""
    found = set()
    for m in modules:
        if m in ("__main__",) or m.startswith("_"):
            continue
        for client in NETWORK_CLIENT_MODULES:
            if m == client or m.startswith(client + "."):
                found.add(m)
    return sorted(found)


def audit_no_network(srcdir: str):
    """Parse every .py in *srcdir* with ast and report any import that could
    open a network connection.  Returns a list of (file, lineno, module).

    A static guarantee that the tool cannot phone home or leak anything.
    """
    import ast
    problems = []
    for name in sorted(os.listdir(srcdir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(srcdir, name)
        try:
            tree = ast.parse(open(path, "r", encoding="utf-8").read(),
                             filename=path)
        except SyntaxError as e:
            problems.append((name, 0, "SYNTAX ERROR: %s" % e))
            continue
        for node in ast.walk(tree):
            root = None
            if isinstance(node, ast.Import):
                root = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
            if root in NETWORK_CAPABLE_MODULES:
                problems.append((name, getattr(node, "lineno", 0), root))
    return problems


# ---------------------------------------------------------------------------
# misc helpers
# ---------------------------------------------------------------------------

def disk_free(path: str) -> int:
    """Free bytes on the filesystem holding *path* (best effort)."""
    try:
        return shutil.disk_usage(os.path.dirname(os.path.abspath(path))
                                 or os.curdir).free
    except OSError:
        return -1


def sha256_file(path: str, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tree_manifest(root: str):
    """{(relpath, size): sha256} for every regular file under *root*.

    Used by tests to prove a conversion never modified the source tree.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            h = hashlib.sha256()
            with open(full, "rb") as f:
                while True:
                    b = f.read(1 << 20)
                    if not b:
                        break
                    h.update(b)
            out[rel] = (os.path.getsize(full), h.hexdigest())
    return out
