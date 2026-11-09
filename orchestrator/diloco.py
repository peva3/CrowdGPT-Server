"""DiLoCo-style outer optimizer over compressed pseudogradients."""
from __future__ import annotations

import numpy as np

from .compression import compress_delta, decompress_delta


def outer_step(params, pseudograd, *, lr: float, momentum: float = 0.9,
               nesterov: bool = True, buffer=None):
    """One outer SGD step with persistent momentum.

    ``buffer`` is the previous momentum term (None means zero). Passing it back
    on the next round is what makes this DiLoCo rather than per-round sign
    descent; the caller must persist it across rounds.
    """
    p = np.asarray(params, dtype=np.float32)
    g = np.asarray(pseudograd, dtype=np.float32)
    prev = np.zeros_like(g) if buffer is None else np.asarray(buffer, dtype=np.float32)
    buf = momentum * prev + g
    update = g + momentum * buf if nesterov else buf
    return p - lr * update, buf


def compress_pseudograd(pseudograd) -> bytes:
    return compress_delta(pseudograd)


def decompress_pseudograd(blob: bytes):
    return decompress_delta(blob)


def bytes_per_token(compressed_bytes: int, tokens: int) -> float:
    return float(compressed_bytes) / max(1, int(tokens))


__all__ = ["bytes_per_token", "compress_pseudograd", "decompress_pseudograd", "outer_step"]
