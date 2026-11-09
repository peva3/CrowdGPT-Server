#!/usr/bin/env python3
"""Benchmark the public krum_stream reference at several model sizes.

The reference is correctness-first and loads full float32 vectors. This tool
measures real time and peak RSS for 1M, 8M, and 32M parameters, then reports a
linear projection for the 657,787,392-parameter model. The projection is not a
measurement; running the full model needs ~2.6 GB per delta and is left to the
operator.
"""
from __future__ import annotations

import argparse
import json
import resource
import struct
import sys
import tempfile
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from krum_stream import open_delta, stream_add_base_delta, streaming_pairwise_sq_l2  # noqa: E402


def _delta(path: Path, params: int) -> None:
    meta = json.dumps({"tokensProcessed": 1000, "loss": 2.5}).encode()
    with path.open("wb") as f:
        f.write(struct.pack("<I", len(meta)))
        f.write(meta)
        f.write(b"\x00\x00" * params)


def measure(params: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        paths = [Path(tmp) / "a.bin", Path(tmp) / "b.bin"]
        for p in paths:
            _delta(p, params)
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        t0 = time.perf_counter()
        dfs = [open_delta(str(p), params) for p in paths]
        dist, bad, sq = streaming_pairwise_sq_l2(dfs, max_ram_bytes=4 * 1024**3)
        weights = torch.zeros(params)
        stream_add_base_delta(weights, dfs[0], 1.0)
        elapsed = time.perf_counter() - t0
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"params": params, "seconds": round(elapsed, 4),
                "peak_delta_kb": max(0, after - before),
                "distance": float(dist[0, 1]), "bad": list(bad),
                "finite": bool(torch.isfinite(weights).all())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="1000000,8000000,32000000")
    args = parser.parse_args()
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    for n in sizes:
        r = measure(n)
        projected = 657_787_392 / n
        print(json.dumps({**r, "projected_seconds_658m": round(r["seconds"] * projected, 1),
                          "projected_peak_gb_658m": round(r["peak_delta_kb"] / 1024**2 * projected, 2)},
                         sort_keys=True))
    print("note: projection is linear; the reference loads full float32 vectors, "
          "so 658M is expected to need several GB per delta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
