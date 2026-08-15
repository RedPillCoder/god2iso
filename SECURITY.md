# Security

## Reporting a vulnerability

This project takes security seriously.  If you find a vulnerability in
god2iso — a parsing bug that could be abused by a crafted image, a path
traversal, an unsafe temp-file behavior, or anything else — please report it
privately:

- **Preferred:** open a *private vulnerability report* on GitHub:
  `https://github.com/<your-user>/god2iso/security/advisories/new`
- Or email the maintainer (link on your profile) with the subject
  `[god2iso security] ...`

Please include:
- the affected version / commit,
- a minimal reproducer (a crafted input file, or a description of the
  layout that triggers it),
- expected vs actual behavior.

We aim to acknowledge reports within 72 hours and to publish a fix + advisory
as soon as possible.

## Security model

- **Fully offline** — the tool never touches the network.  `god2iso.py audit`
  (and the packaged exe's embedded-audit) verifies this mechanically: no
  network-capable imports exist in the source or the bundle.
- **Non-destructive** — inputs are opened read-only; output is written to a
  temp file and atomically renamed; existing files are never overwritten
  without `--force`; outputs are refused if they would clobber an input.
- **Untrusted input** — `.live` headers, part files and ISO directory tables
  are treated as untrusted: sizes are bounded, directory depth/table size are
  capped, path traversal / NUL / control chars / Windows reserved names are
  rejected, symlink escapes and case-collisions are refused during extract.
- **No key material** — the tool deliberately contains no encryption keys or
  decryption code; retail-encrypted content is detected and reported, not
  decrypted.
- **Reproducible builds** — `build_windows.bat` + the `.spec` files rebuild
  the exe from source; the release SHA-256 lets users verify provenance.

## Supported versions

Only the latest release is supported for security fixes.  Users are advised
to always run the newest `release/god2iso.exe`.
