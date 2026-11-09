#!/usr/bin/env python3
"""Local compatibility checks for the documented upgrade matrix."""
from __future__ import annotations

import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "server.py").read_text()


def check(label, ok):
    print(("OK   " if ok else "FAIL ") + label)
    return bool(ok)


def main():
    ok = True
    ok &= check("API version ng-api-2", 'API_VERSION = "ng-api-2"' in SRC)
    ok &= check("version gate returns 426", "status_code=426" in SRC and "_version_gate" in SRC)
    ok &= check("slowapi is optional", "except ImportError" in SRC or "try:" in SRC and "_HAS_SLOWAPI" in SRC)
    ok &= check("peers are optional", "CROWDGPT_PEERS" in SRC and "PEER = None" in SRC)
    ok &= check("eval gate exists", "REQUIRE_EVAL" in SRC and "_eval_configured" in SRC)
    ok &= check("admin header path exists", "X-Admin-Secret" in SRC)
    import sys
    sys.path.insert(0, str(ROOT))
    from orchestrator.state import StateStore
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp) / "state.sqlite3")
        ok &= check("state schema opens and versions", store.schema_version() >= 1)
        store.close()
    from tools.backup_restore import backup, restore, verify
    ok &= check("backup/restore/verify callable", all(callable(f) for f in (backup, restore, verify)))
    print("RESULT:", "compatible" if ok else "incompatible")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
