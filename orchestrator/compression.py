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
    padded = np.zeros(nb * block, dtype=np.float32)
    padded[:n] = arr
    matrix = padded.reshape(nb, block)
    counts = np.full(nb, block, dtype=np.float64)
    counts[-1] = n - (nb - 1) * block
    scales = (np.abs(matrix).sum(axis=1) / counts).astype(np.float16)
    bits = (matrix >= 0).astype(np.uint8)
    packed = np.packbits(bits.reshape(-1), bitorder="little").tobytes()
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


FRAME_MAGIC = b"NGD2"


def pack_frames(blocks) -> bytes:
    """Frame several independent 1-bit blocks under one container."""
    frames = [compress_delta(block) for block in blocks]
    total = sum(int(np.asarray(block).size) for block in blocks)
    parts = [FRAME_MAGIC, struct.pack("<QI", total, len(frames))]
    for frame in frames:
        parts.append(struct.pack("<I", len(frame)))
        parts.append(frame)
    return b"".join(parts)


def iter_frames(blob: bytes):
    """Yield decoded blocks; raises on a truncated or trailing container."""
    if not blob.startswith(FRAME_MAGIC):
        raise ValueError("bad frame magic")
    pos = len(FRAME_MAGIC)
    total, count = struct.unpack_from("<QI", blob, pos); pos += 12
    seen = 0
    for _ in range(count):
        if len(blob) < pos + 4:
            raise ValueError("truncated frame header")
        (size,) = struct.unpack_from("<I", blob, pos); pos += 4
        frame = blob[pos:pos + size]
        if len(frame) != size:
            raise ValueError("truncated frame")
        block = decompress_delta(frame)
        seen += int(block.size)
        yield block
        pos += size
    if pos != len(blob):
        raise ValueError("trailing bytes after frames")
    if seen != total:
        raise ValueError(f"frame total mismatch: {seen} != {total}")


def unpack_frames(blob: bytes) -> np.ndarray:
    parts = list(iter_frames(blob))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def compression_ratio(original_bytes: int, compressed: bytes) -> float:
    return float(original_bytes) / max(1, len(compressed))


__all__ = ["BLOCK", "FRAME_MAGIC", "compress_delta", "compression_ratio",
           "decompress_delta", "iter_frames", "pack_frames", "unpack_frames"]
