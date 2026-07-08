"""Engine aggregation, veto policy, and trace-event emission."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.helpers import make_header
from tradewind.errors import InvariantViolation
from tradewind.invariants.checks import default_invariants
from tradewind.invariants.domain import (
    MarketContext,
    ProposedAction,
    RiskConfig,
    Side,
)
from tradewind.invariants.engine import InvariantEngine, VetoPolicy, record_evaluation
from tradewind.invariants.portfolio import PortfolioState
from tradewind.trace.reader import read_trace
from tradewind.trace.writer import TraceWriter

NOW = datetime(2024, 1, 2, tzinfo=UTC)
UNIVERSE = frozenset({"AAPL", "MSFT"})


def risk() -> RiskConfig:
    return RiskConfig(
        max_position_per_symbol=Decimal("100"),
        max_gross_exposure=Decimal("100000"),
        max_drawdown=Decimal("0.20"),
        price_sanity_pct=Decimal("0.10"),
        max_orders_per_window=5,
        rate_window_seconds=3600,
    )


def market() -> MarketContext:
    return MarketContext(
        now=NOW,
        last_price={"AAPL": Decimal("100"), "MSFT": Decimal("200")},
        tradeable_universe=UNIVERSE,
    )


def engine(policy: VetoPolicy = VetoPolicy.SUPPRESS) -> InvariantEngine:
    return InvariantEngine(default_invariants(risk()), policy=policy)


def test_clean_action_passes_all_six() -> None:
    result = engine().evaluate(
        PortfolioState.initial(Decimal("100000")),
        ProposedAction("AAPL", Side.BUY, Decimal("1")),
        market(),
    )
    assert not result.blocked
    assert len(result.verdicts) == 6
    assert not result.vetoes


def test_bad_action_is_blocked_and_names_the_invariant() -> None:
    result = engine().evaluate(
        PortfolioState.initial(Decimal("100000")),
        ProposedAction("NOPE", Side.BUY, Decimal("1")),
        market(),
    )
    assert result.blocked
    assert any(v.invariant == "order_validity" for v in result.vetoes)


def test_halt_policy_raises() -> None:
    with pytest.raises(InvariantViolation, match="HALT policy"):
        engine(VetoPolicy.HALT).evaluate(
            PortfolioState.initial(Decimal("100000")),
            ProposedAction("NOPE", Side.BUY, Decimal("1")),
            market(),
        )


def test_suppress_policy_does_not_raise() -> None:
    result = engine(VetoPolicy.SUPPRESS).evaluate(
        PortfolioState.initial(Decimal("100000")),
        ProposedAction("NOPE", Side.BUY, Decimal("1")),
        market(),
    )
    assert result.blocked  # reported, not raised


def test_record_evaluation_emits_check_and_violation_events(tmp_path: Path) -> None:
    result = engine().evaluate(
        PortfolioState.initial(Decimal("100000")),
        ProposedAction("NOPE", Side.BUY, Decimal("1")),
        market(),
    )
    trace = tmp_path / "eval.jsonl"
    with TraceWriter(trace, make_header()) as writer:
        action_event = writer.append("proposed_action", {"symbol": "NOPE"})
        events = record_evaluation(writer, result, parent_seq=action_event.seq)

    assert events[0].event_type == "invariant_check"
    assert events[0].payload["blocked"] is True
    violations = events[1:]
    assert violations and all(e.event_type == "violation" for e in violations)
    # Violations link back to the check event; the trace verifies end-to-end.
    assert all(e.parent_seq == events[0].seq for e in violations)
    _, replayed = read_trace(trace)
    assert [e.event_type for e in replayed][:2] == ["proposed_action", "invariant_check"]
