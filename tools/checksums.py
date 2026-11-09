#!/usr/bin/env python3
"""Write or verify a SHA-256 manifest for release artifacts.

    python tools/checksums.py write dist/*
    python tools/checksums.py verify dist/SHA256SUMS

Paths are recorded relative to the manifest's directory. Verification fails
closed on a missing file, a changed digest, or a malformed line.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write(paths: list[str]) -> int:
    if not paths:
        print("no artifacts given", file=sys.stderr)
        return 2
    out = Path("SHA256SUMS")
    lines = []
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            print(f"not a file: {p}", file=sys.stderr)
            return 2
        lines.append(f"{digest(p)}  {p.name}")
    out.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    print(f"wrote {out} with {len(lines)} entries")
    return 0


def verify(manifest: str) -> int:
    path = Path(manifest)
    base = path.parent
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            print(f"malformed: {line}", file=sys.stderr)
            bad += 1
            continue
        want, name = parts
        target = base / name
        if not target.is_file():
            print(f"missing: {name}", file=sys.stderr)
            bad += 1
            continue
        got = digest(target)
        if got != want:
            print(f"mismatch: {name}", file=sys.stderr)
            bad += 1
    if bad:
        print(f"FAILED: {bad} problem(s)", file=sys.stderr)
        return 1
    print("OK: all artifacts match")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("write", "verify"):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[1] == "write":
        return write(argv[2:])
    return verify(argv[2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
