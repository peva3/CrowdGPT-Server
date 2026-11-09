"""Admission policy is configuration, not code."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .nodes import Node
from .reputation import ReputationService

POLICIES = ("open", "bonded", "vetted")


@dataclass(frozen=True)
class AdmissionPolicy:
    mode: str = "open"
    bond_rounds: int = 3
    min_version: str = "3.2"

    @classmethod
    def from_env(cls) -> "AdmissionPolicy":
        mode = (os.getenv("CROWDGPT_ADMISSION_POLICY", "open") or "open").lower()
        if mode not in POLICIES: mode = "open"
        try: bond = int(os.getenv("CROWDGPT_BOND_ROUNDS", "3"))
        except ValueError: bond = 3
        return cls(mode, max(0, bond), os.getenv("CROWDGPT_MIN_NODE_VERSION", "3.2"))

    def version_ok(self, node: Node) -> bool:
        def parts(v):
            try: return tuple(int(x) for x in str(v).split(".")[:3])
            except ValueError: return (0,)
        return parts(node.record.get("software_version")) >= parts(self.min_version)

    def allowed(self, node: Node, reputation: ReputationService,
                *, vouched: bool = False) -> tuple[bool, str]:
        if node.status != "active": return False, "node_not_active"
        if not self.version_ok(node): return False, "incompatible_version"
        if self.mode == "open": return True, "open"
        if self.mode == "bonded":
            return (True, "bonded") if reputation.bond_satisfied(node, required_rounds=self.bond_rounds) \
                else (False, "bond_not_satisfied")
        if self.mode == "vetted":
            return (True, "vetted") if vouched else (False, "not_vetted")
        return False, "unknown_policy"


__all__ = ["AdmissionPolicy", "POLICIES"]
