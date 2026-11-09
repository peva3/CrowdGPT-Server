"""Structured per-round metrics with a hard no-IP / no-secret rule.

The recorder owns one append-only JSONL file. Keys that could identify a
person or carry a credential are dropped before writing; callers pass only
aggregate counts. This is intentionally separate from public_log.py, which
already publishes sanitized events.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

DENY_KEYS = {
    "ip", "client_ip", "remote_addr", "x_forwarded_for", "forwarded",
    "authorization", "secret", "token", "api_key", "password", "cookie",
}


def scrub(value):
    if isinstance(value, dict):
        return {str(k): scrub(v) for k, v in value.items()
                if str(k).lower() not in DENY_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and len(value) > 512:
        return value[:512]
    return value


@dataclass(frozen=True)
class RoundMetric:
    schema: str
    round: int
    step: int
    opened_at: str
    closed_at: str
    duration_s: float
    tokens: int
    deltas: int
    accepted: int
    rejected: int
    bytes_down: int
    bytes_up: int
    eval_loss: Optional[str] = None
    eval_n: Optional[int] = None
    client_versions: tuple = field(default_factory=tuple)
    gpu_classes: tuple = field(default_factory=tuple)
    certified: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        return scrub(data)


class MetricsRecorder:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def append(self, metric: RoundMetric) -> None:
        if metric.schema != "ng-metric-1":
            raise ValueError("unsupported metric schema")
        if metric.tokens < 0 or metric.deltas < 0 or metric.bytes_down < 0:
            raise ValueError("negative metric")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(metric.to_dict(), sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self) -> Iterable[dict]:
        if not self.path.exists():
            return []
        rows = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def summary(self) -> dict:
        rows = list(self.read())
        if not rows:
            return {"rounds": 0}
        return {
            "rounds": len(rows),
            "tokens": sum(int(r.get("tokens", 0)) for r in rows),
            "deltas": sum(int(r.get("deltas", 0)) for r in rows),
            "rejected": sum(int(r.get("rejected", 0)) for r in rows),
            "bytes_up": sum(int(r.get("bytes_up", 0)) for r in rows),
            "certified": sum(1 for r in rows if r.get("certified")),
        }


__all__ = ["MetricsRecorder", "RoundMetric", "scrub"]
