"""The invariant engine: run the pipeline, aggregate verdicts, emit events.

Contract: :meth:`InvariantEngine.evaluate` is pure — it runs every invariant
over ``(state, action, ctx)`` and returns an :class:`EvaluationResult`; it does
not mutate state or write anything. The engine is the sole authority on whether
an action is admitted: if any verdict is a ``VETO`` the action is *blocked*, and
no LLM output can override that. Trace emission is a separate, explicit step
(:func:`record_evaluation`) so the pure core never depends on the writer.

Veto policy:

* ``SUPPRESS`` (default) — a blocked action is dropped; the run continues.
* ``HALT`` — a blocked action raises :class:`InvariantViolation`, stopping the run.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from tradewind.errors import InvariantViolation
from tradewind.invariants.checks import Invariant
from tradewind.invariants.domain import MarketContext, ProposedAction
from tradewind.invariants.portfolio import PortfolioState
from tradewind.invariants.verdict import Decision, Verdict
from tradewind.trace.events import TraceEvent
from tradewind.trace.writer import TraceWriter


class VetoPolicy(Enum):
    """What the engine does when an action is blocked."""

    SUPPRESS = "suppress"
    HALT = "halt"


@dataclass(frozen=True)
class EvaluationResult:
    """The full set of verdicts for one action, plus the admission decision."""

    verdicts: tuple[Verdict, ...]

    @property
    def vetoes(self) -> tuple[Verdict, ...]:
        """Verdicts that block the action."""
        return tuple(v for v in self.verdicts if v.decision is Decision.VETO)

    @property
    def warnings(self) -> tuple[Verdict, ...]:
        """Non-blocking warnings."""
        return tuple(v for v in self.verdicts if v.decision is Decision.WARN)

    @property
    def blocked(self) -> bool:
        """Whether at least one invariant vetoed the action."""
        return bool(self.vetoes)

    def to_payload(self) -> dict[str, object]:
        """JSON-native summary for an ``invariant_check`` event."""
        return {
            "blocked": self.blocked,
            "verdicts": [v.to_payload() for v in self.verdicts],
        }


class InvariantEngine:
    """Runs a fixed pipeline of invariants and rules on each proposed action."""

    def __init__(
        self, invariants: Sequence[Invariant], policy: VetoPolicy = VetoPolicy.SUPPRESS
    ) -> None:
        self._invariants = tuple(invariants)
        self.policy = policy

    @property
    def invariants(self) -> tuple[Invariant, ...]:
        """The configured pipeline, in evaluation order."""
        return self._invariants

    def evaluate(
        self, state: PortfolioState, action: ProposedAction, ctx: MarketContext
    ) -> EvaluationResult:
        """Run every invariant over the action; return all verdicts (pure)."""
        verdicts = tuple(inv.check(state, action, ctx) for inv in self._invariants)
        result = EvaluationResult(verdicts=verdicts)
        if result.blocked and self.policy is VetoPolicy.HALT:
            reasons = "; ".join(f"{v.invariant}: {v.reason}" for v in result.vetoes)
            raise InvariantViolation(
                f"action on {action.symbol} vetoed under HALT policy: {reasons}"
            )
        return result


def record_evaluation(
    writer: TraceWriter,
    result: EvaluationResult,
    parent_seq: int | None = None,
) -> list[TraceEvent]:
    """Append an ``invariant_check`` event plus one ``violation`` per veto.

    Returns the events written (the check event first). The ``violation``
    events carry each vetoing verdict's full evidence and link back to the
    check via ``parent_seq``, so a report can trace a block to its cause.
    """
    check_event = writer.append("invariant_check", result.to_payload(), parent_seq)
    events = [check_event]
    for veto in result.vetoes:
        events.append(writer.append("violation", veto.to_payload(), parent_seq=check_event.seq))
    return events
