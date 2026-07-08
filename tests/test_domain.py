"""Domain value objects: payload round-trips, projection edges, verdict helpers."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradewind.invariants.domain import (
    Fill,
    MarketContext,
    MarketModel,
    ProposedAction,
    Side,
)
from tradewind.invariants.portfolio import PortfolioState
from tradewind.invariants.verdict import Decision, Verdict

NOW = datetime(2024, 1, 2, tzinfo=UTC)


def test_proposed_action_payload_roundtrip() -> None:
    action = ProposedAction("AAPL", Side.SELL, Decimal("2.5"), limit_price=Decimal("99.5"))
    restored = ProposedAction.from_payload(action.to_payload())
    assert restored == action


def test_proposed_action_payload_roundtrip_no_limit() -> None:
    action = ProposedAction("MSFT", Side.BUY, Decimal("3"))
    payload = action.to_payload()
    assert payload["limit_price"] is None
    assert ProposedAction.from_payload(payload) == action


def test_from_payload_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="missing side/quantity"):
        ProposedAction.from_payload({"symbol": "AAPL", "side": None, "quantity": "1"})


def test_fill_payload_shape() -> None:
    fill = Fill.honest("AAPL", Side.BUY, Decimal("5"), Decimal("100"), Decimal("1"))
    payload = fill.to_payload()
    assert payload["cash_delta"] == "-501"
    assert payload["position_delta"] == "5"


def test_project_fill_none_cases() -> None:
    ctx = MarketContext(
        now=NOW,
        last_price={"AAPL": Decimal("100")},
        tradeable_universe=frozenset({"AAPL"}),
    )
    assert (
        ctx.project_fill(ProposedAction("NOPE", Side.BUY, Decimal("1"))) is None
    )  # not in universe
    assert ctx.project_fill(ProposedAction("AAPL", Side.BUY, Decimal("0"))) is None  # non-positive
    assert ctx.project_fill(ProposedAction("AAPL", Side.BUY, Decimal("NaN"))) is None  # non-finite


def test_project_fill_applies_slippage_and_fees() -> None:
    ctx = MarketContext(
        now=NOW,
        last_price={"AAPL": Decimal("100")},
        tradeable_universe=frozenset({"AAPL"}),
        model=MarketModel(fee_bps=Decimal("10"), slippage_bps=Decimal("50")),
    )
    fill = ctx.project_fill(ProposedAction("AAPL", Side.BUY, Decimal("10")))
    assert fill is not None
    assert fill.price == Decimal("100.50")  # +50 bps slippage on a buy
    assert fill.fees == Decimal("100.50") * Decimal("10") * Decimal("0.001")


def test_gross_exposure_requires_price() -> None:
    state = PortfolioState.initial(Decimal("1000")).apply_fill(
        Fill.honest("AAPL", Side.BUY, Decimal("1"), Decimal("100"), Decimal("0"))
    )
    with pytest.raises(ValueError, match="no price"):
        state.gross_exposure({"MSFT": Decimal("10")})


def test_verdict_helpers() -> None:
    warn = Verdict.warn("x", "careful", detail="close to cap")
    assert warn.decision is Decision.WARN and not warn.is_veto
    veto = Verdict.veto("x", "no", value="7")
    assert veto.is_veto
    assert veto.to_payload()["evidence"] == {"value": "7"}
