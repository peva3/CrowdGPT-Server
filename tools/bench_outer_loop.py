#!/usr/bin/env python3
"""Compare per-round delta averaging with a DiLoCo-style outer loop.

Synthetic quadratic problem, equal local work and equal tokens per round. This
checks the mechanics and communication accounting, not production model loss.
It imports the server package, so the server checkout must be present.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

SERVER = Path(__file__).resolve().parents[2] / "CrowdGPT-Server"
sys.path.insert(0, str(SERVER))
from orchestrator import diloco  # noqa: E402


def run(mode: str, rounds: int, workers: int, local_steps: int, lr: float, dim: int = 8,
        seed: int = 7, outer_lr: float = 0.2):
    rng = np.random.default_rng(seed)
    targets = rng.normal(size=(workers, dim)).astype(np.float32)
    w = np.zeros(dim, dtype=np.float32)
    buffer = None
    tokens = 0
    communicated = 0
    for _ in range(rounds):
        locals_ = []
        for i in range(workers):
            local = w.copy()
            for _step in range(local_steps):
                local -= lr * (local - targets[i])
                tokens += 1
            locals_.append(local)
        mean_delta = np.mean([local - w for local in locals_], axis=0).astype(np.float32)
        if mode == "average":
            w = (w + mean_delta).astype(np.float32)
        elif mode == "diloco":
            pseudo = (-mean_delta).astype(np.float32)
            blob = diloco.compress_pseudograd(pseudo)
            decoded = diloco.decompress_pseudograd(blob)
            communicated += len(blob)
            w, buffer = diloco.outer_step(w, decoded, lr=outer_lr, momentum=0.9,
                                          nesterov=True, buffer=buffer)
        else:
            raise ValueError(mode)
    loss = float(np.mean([0.5 * np.sum((w - t) ** 2) for t in targets]))
    return loss, tokens, communicated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--local-steps", type=int, default=20)
    parser.add_argument("--lr", type=float, default=0.1)
    parser.add_argument("--outer-lr", type=float, default=0.2)
    args = parser.parse_args()
    print(f"rounds={args.rounds} workers={args.workers} local_steps={args.local_steps} "
          f"lr={args.lr} outer_lr={args.outer_lr} tokens={args.rounds*args.workers*args.local_steps}")
    for mode in ("average", "diloco"):
        loss, tokens, _ = run(mode, args.rounds, args.workers, args.local_steps, args.lr,
                              outer_lr=args.outer_lr)
        print(f"{mode:8s} final_loss={loss:.6f} tokens={tokens}")
    blob = diloco.compress_pseudograd(np.full(8, 0.25, dtype=np.float32))
    print(f"compressed_pseudograd_bytes={len(blob)} bytes_per_token={diloco.bytes_per_token(len(blob), args.workers*args.local_steps):.4f}")
    print("scope: synthetic quadratic, equal local steps/tokens; not production loss")


if __name__ == "__main__":
    main()
