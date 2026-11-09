"""DiLoCo-style outer optimizer over compressed pseudogradients."""
from __future__ import annotations

import numpy as np

from .compression import compress_delta, decompress_delta


def outer_step(params, pseudograd, *, lr: float, momentum: float = 0.9,
               nesterov: bool = True):
    """One outer SGD step. ``params``/``pseudograd`` are flat float arrays."""
    p = np.asarray(params, dtype=np.float32)
    g = np.asarray(pseudograd, dtype=np.float32)
    buf = momentum * g
    update = g + momentum * buf if nesterov else buf
    return p - lr * update, buf


def compress_pseudograd(pseudograd) -> bytes:
    return compress_delta(pseudograd)


def decompress_pseudograd(blob: bytes):
    return decompress_delta(blob)


def bytes_per_token(compressed_bytes: int, tokens: int) -> float:
    return float(compressed_bytes) / max(1, int(tokens))


__all__ = ["bytes_per_token", "compress_pseudograd", "decompress_pseudograd", "outer_step"]
