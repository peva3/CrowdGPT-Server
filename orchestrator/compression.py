"""1-bit delta compression with per-block scales plus zlib framing.

A block of 1024 params becomes one fp16 scale plus 1024 sign bits (128 bytes).
For 657,787,392 params that is ~80.3 MB before entropy coding, under the
200 MB target. The codec is deliberately simple so a client and a validator
can reproduce it byte-for-byte.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

BLOCK = 1024
MAGIC = b"NGD1"


def compress_delta(values, *, block: int = BLOCK, level: int = 6) -> bytes:
    arr = np.asarray(values, dtype=np.float32).reshape(-1)
    n = arr.size
    nb = (n + block - 1) // block
    scales = np.zeros(nb, dtype=np.float16)
    bits = np.zeros(nb * block, dtype=np.uint8)
    for b in range(nb):
        chunk = arr[b * block:(b + 1) * block]
        scale = float(np.mean(np.abs(chunk))) if chunk.size else 0.0
        scales[b] = scale
        signs = chunk >= 0
        bits[b * block:b * block + chunk.size] = signs.astype(np.uint8)
    packed = np.packbits(bits, bitorder="little").tobytes()
    raw = MAGIC + struct.pack("<QI", n, block) + scales.tobytes() + packed
    return zlib.compress(raw, level)


def decompress_delta(blob: bytes) -> np.ndarray:
    raw = zlib.decompress(blob)
    if not raw.startswith(MAGIC): raise ValueError("bad delta magic")
    pos = len(MAGIC)
    n, block = struct.unpack_from("<QI", raw, pos); pos += 12
    nb = (n + block - 1) // block
    scales = np.frombuffer(raw, dtype=np.float16, count=nb, offset=pos); pos += nb * 2
    packed = np.frombuffer(raw, dtype=np.uint8, offset=pos)
    bits = np.unpackbits(packed, bitorder="little")[:nb * block]
    out = np.where(bits == 1, scales.repeat(block), -scales.repeat(block))[:n]
    return out.astype(np.float32)


def compression_ratio(original_bytes: int, compressed: bytes) -> float:
    return float(original_bytes) / max(1, len(compressed))


__all__ = ["BLOCK", "compress_delta", "compression_ratio", "decompress_delta"]
