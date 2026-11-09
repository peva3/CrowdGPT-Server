"""Rank-16 LoRA adapters: serialization, merge, and size accounting."""
from __future__ import annotations

import numpy as np

RANK = 16
ALPHA = 32.0


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


__all__ = ["ALPHA", "RANK", "adapter_bytes", "delta", "deserialize", "init_adapter",
           "merge", "serialize"]
