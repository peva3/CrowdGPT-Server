"""Rank-16 LoRA adapters: serialization, merge, wire format, and size accounting.

The wire payload is deliberately independent of the client's model code: a
client sends each adapter's target name, the offset of that base weight in the
flat parameter stream, its shape, and the two fp16 factors.  The orchestrator
expands the adapters into a dense delta before the existing cross-check and
aggregation pipeline runs.  That keeps the existing dense aggregation path
unchanged while cutting the upload from ~1.3 GB to ~19 MB at rank 16.
"""
from __future__ import annotations

import struct

import numpy as np

RANK = 16
ALPHA = 32.0
WIRE_MAGIC = b"NGL1"
WIRE_HEADER = struct.Struct("<HIIIH")  # name_len, offset, rows, cols, rank


def init_adapter(out_features: int, in_features: int, *, rank: int = RANK,
                 seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((rank, in_features), dtype=np.float32) * 0.01
    b = np.zeros((out_features, rank), dtype=np.float32)
    return a, b


def delta(a: np.ndarray, b: np.ndarray, *, alpha: float = ALPHA) -> np.ndarray:
    rank = a.shape[0]
    return (b @ a) * (alpha / rank)


def merge(base: np.ndarray, a: np.ndarray, b: np.ndarray, *, alpha: float = ALPHA) -> np.ndarray:
    return np.asarray(base, dtype=np.float32) + delta(a, b, alpha=alpha)


def serialize(a: np.ndarray, b: np.ndarray) -> bytes:
    a16 = np.asarray(a, dtype=np.float16); b16 = np.asarray(b, dtype=np.float16)
    return b"LRA1" + np.int32(a16.shape[0]).tobytes() + np.int32(a16.shape[1]).tobytes() + \
        np.int32(b16.shape[0]).tobytes() + a16.tobytes() + b16.tobytes()


def deserialize(blob: bytes) -> tuple[np.ndarray, np.ndarray]:
    if not blob.startswith(b"LRA1"): raise ValueError("bad LoRA magic")
    r, i, o = np.frombuffer(blob, dtype=np.int32, count=3, offset=4)
    pos = 16
    a = np.frombuffer(blob, dtype=np.float16, count=r * i, offset=pos).reshape(r, i).astype(np.float32)
    pos += r * i * 2
    b = np.frombuffer(blob, dtype=np.float16, count=o * r, offset=pos).reshape(o, r).astype(np.float32)
    return a, b


def adapter_bytes(out_features: int, in_features: int, *, rank: int = RANK) -> int:
    return (rank * in_features + out_features * rank) * 2 + 16


def encode_payload(adapters: list[dict], *, alpha: float = ALPHA) -> bytes:
    """Encode ``[{name, offset, a, b}, ...]`` into the compact upload payload.

    Offsets are validated here so a malformed client cannot write outside the
    model.  Names are informational; the offset is the authoritative location.
    """
    parts = [WIRE_MAGIC, struct.pack("<I", len(adapters))]
    for item in adapters:
        name = str(item["name"]).encode("utf-8")
        if not name or len(name) > 65535:
            raise ValueError("adapter name length out of range")
        a = np.asarray(item["a"], dtype=np.float16)
        b = np.asarray(item["b"], dtype=np.float16)
        if a.ndim != 2 or b.ndim != 2 or a.shape[0] != b.shape[1]:
            raise ValueError(f"{item['name']}: incompatible adapter shapes")
        rows, cols = int(b.shape[0]), int(a.shape[1])
        rank = int(a.shape[0])
        offset = int(item["offset"])
        if offset < 0 or rows <= 0 or cols <= 0 or rank <= 0:
            raise ValueError(f"{item['name']}: invalid shape or offset")
        parts.append(WIRE_HEADER.pack(len(name), offset, rows, cols, rank))
        parts.append(name)
        parts.append(a.tobytes(order="C"))
        parts.append(b.tobytes(order="C"))
    return b"".join(parts)


def decode_payload(blob: bytes, *, model_size: int) -> list[dict]:
    """Parse and bounds-check a wire payload.  Does not allocate the dense delta."""
    if not blob.startswith(WIRE_MAGIC):
        raise ValueError("bad LoRA payload magic")
    pos = len(WIRE_MAGIC)
    if len(blob) < pos + 4:
        raise ValueError("truncated LoRA payload")
    count = struct.unpack_from("<I", blob, pos)[0]
    pos += 4
    if count <= 0 or count > 4096:
        raise ValueError("LoRA adapter count out of range")
    adapters = []
    spans = []
    for _ in range(count):
        if len(blob) < pos + WIRE_HEADER.size:
            raise ValueError("truncated LoRA adapter header")
        nlen, offset, rows, cols, rank = WIRE_HEADER.unpack_from(blob, pos)
        pos += WIRE_HEADER.size
        name = blob[pos:pos + nlen].decode("utf-8", "strict")
        pos += nlen
        a_n, b_n = rank * cols, rows * rank
        need = (a_n + b_n) * 2
        if len(blob) < pos + need:
            raise ValueError(f"{name}: truncated adapter factors")
        a = np.frombuffer(blob, dtype=np.float16, count=a_n, offset=pos).reshape(rank, cols).astype(np.float32)
        pos += a_n * 2
        b = np.frombuffer(blob, dtype=np.float16, count=b_n, offset=pos).reshape(rows, rank).astype(np.float32)
        pos += b_n * 2
        if offset + rows * cols > model_size:
            raise ValueError(f"{name}: adapter exceeds model size")
        spans.append((offset, offset + rows * cols))
        adapters.append({"name": name, "offset": offset, "a": a, "b": b})
    if pos != len(blob):
        raise ValueError("trailing bytes after LoRA payload")
    spans.sort()
    for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        if b0 < a1:
            raise ValueError("overlapping LoRA adapter spans")
    return adapters


def expand_to_dense(adapters: list[dict], model_size: int, *, alpha: float = ALPHA) -> np.ndarray:
    """Expand adapters into the dense base-delta stream the current pipeline expects."""
    if model_size <= 0:
        raise ValueError("model_size must be positive")
    out = np.zeros(int(model_size), dtype=np.float32)
    for item in adapters:
        a = np.asarray(item["a"], dtype=np.float32)
        b = np.asarray(item["b"], dtype=np.float32)
        rank = int(a.shape[0])
        rows, cols = int(b.shape[0]), int(a.shape[1])
        offset = int(item["offset"])
        if rank <= 0 or offset < 0 or offset + rows * cols > model_size:
            raise ValueError(f"{item.get('name', '?')}: invalid adapter span")
        out[offset:offset + rows * cols] = (b @ a * (alpha / rank)).reshape(-1)
    return out


def payload_bytes(adapter_items: list[tuple[int, int]], *, rank: int = RANK) -> int:
    """Wire size for a list of ``(out_features, in_features)`` targets."""
    total = len(WIRE_MAGIC) + 4
    for out_features, in_features in adapter_items:
        name_len = len(f"target_{out_features}_{in_features}")
        total += name_len + WIRE_HEADER.size + adapter_bytes(out_features, in_features, rank=rank) - 16
    return total


__all__ = ["ALPHA", "RANK", "WIRE_MAGIC", "adapter_bytes", "decode_payload", "delta",
           "deserialize", "encode_payload", "expand_to_dense", "init_adapter", "merge",
           "payload_bytes", "serialize"]
