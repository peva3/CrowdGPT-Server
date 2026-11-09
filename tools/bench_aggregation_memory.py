#!/usr/bin/env python3
"""Measure the memory floor for one aggregation pass on the real model sizes.

This allocates the 658M base and 350M engram float32 masters, then applies one
10M-parameter delta chunk. It does not load the model, dataset, or network. Run
with the project virtualenv; peak RSS is the number to size an operator node by.
"""
from __future__ import annotations

import resource
import time

import numpy as np
import torch

MODEL_SIZE = 657_787_392
ENG_PARAMS = 350_000_640
CHUNK = 10_000_000


def rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def main() -> None:
    start = time.perf_counter()
    base = torch.zeros(MODEL_SIZE, dtype=torch.float32)
    engram = torch.zeros(ENG_PARAMS, dtype=torch.float32)
    alloc = time.perf_counter() - start
    print(f"base_params={MODEL_SIZE} engram_params={ENG_PARAMS}")
    print(f"master_bytes={(MODEL_SIZE + ENG_PARAMS) * 4:,}")
    print(f"alloc_seconds={alloc:.3f} peak_rss_gb={rss_gb():.2f}")
    chunk_start = time.perf_counter()
    for offset in range(0, MODEL_SIZE, CHUNK):
        end = min(MODEL_SIZE, offset + CHUNK)
        delta = torch.zeros(end - offset, dtype=torch.float32)
        base[offset:end].add_(delta, alpha=0.5)
        del delta
    apply = time.perf_counter() - chunk_start
    print(f"apply_seconds={apply:.3f} peak_rss_gb={rss_gb():.2f}")
    print(f"base_norm={float(base.norm()):.1f} engram_norm={float(engram.norm()):.1f}")


if __name__ == "__main__":
    main()
