#!/usr/bin/env python3
"""Emit a small CycloneDX-style SBOM from installed distributions."""
from __future__ import annotations

import json
import platform
import sys
from importlib import metadata


def main() -> int:
    components = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"]
        if not name:
            continue
        components.append({"type": "library", "name": name,
                           "version": dist.version or "unknown"})
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
           "metadata": {"component": {"type": "application",
                                      "name": "crowdgpt-orchestrator",
                                      "version": "ng-api-2"},
                        "properties": [{"name": "python", "value": sys.version.split()[0]},
                                       {"name": "platform", "value": platform.platform()}]},
           "components": components}
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
