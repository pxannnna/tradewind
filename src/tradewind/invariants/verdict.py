"""Verdicts: the deterministic output of every invariant check.

Contract: an invariant returns exactly one :class:`Verdict`. A ``VETO`` blocks
the action and must carry a human-readable ``reason`` plus machine-readable
``evidence`` (all JSON-native, money as decimal strings) so a ``violation``
trace event fully explains itself. Verdicts are frozen value objects; they
perform no I/O.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class Decision(Enum):
    """The three possible outcomes of an invariant check."""

    PASS = "pass"
    WARN = "warn"
    VETO = "veto"


@dataclass(frozen=True)
class Verdict:
    """One invariant's ruling on one proposed action."""

    invariant: str
    decision: Decision
    reason: str = ""
    evidence: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    @classmethod
    def passed(cls, invariant: str) -> "Verdict":
        """Build a ``PASS`` verdict."""
        return cls(invariant=invariant, decision=Decision.PASS)

    @classmethod
    def warn(cls, invariant: str, reason: str, **evidence: str | int | bool | None) -> "Verdict":
        """Build a ``WARN`` verdict with evidence."""
        return cls(invariant=invariant, decision=Decision.WARN, reason=reason, evidence=evidence)

    @classmethod
    def veto(cls, invariant: str, reason: str, **evidence: str | int | bool | None) -> "Verdict":
        """Build a ``VETO`` verdict with evidence."""
        return cls(invariant=invariant, decision=Decision.VETO, reason=reason, evidence=evidence)

    @property
    def is_veto(self) -> bool:
        """Whether this verdict blocks the action."""
        return self.decision is Decision.VETO

    def to_payload(self) -> dict[str, object]:
        """JSON-native form for an ``invariant_check`` / ``violation`` event."""
        return {
            "invariant": self.invariant,
            "decision": self.decision.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }
