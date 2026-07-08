"""Portfolio accounting: cash conservation, apply/mark, idempotent replay."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tradewind.errors import InvariantViolation
from tradewind.invariants.domain import Fill, Side
from tradewind.invariants.portfolio import (
    PortfolioState,
    check_fill_conservation,
    replay_fills,
    verify_fill_consistency,
)

prices = st.decimals(
    min_value="0.01", max_value="10000", allow_nan=False, allow_infinity=False, places=2
)
qtys = st.decimals(
    min_value="0.001", max_value="10000", allow_nan=False, allow_infinity=False, places=3
)
fees = st.decimals(min_value="0", max_value="100", allow_nan=False, allow_infinity=False, places=4)
sides = st.sampled_from([Side.BUY, Side.SELL])


def honest(symbol: str, side: Side, qty: str, price: str, fee: str = "0") -> Fill:
    return Fill.honest(symbol, side, Decimal(qty), Decimal(price), Decimal(fee))


def test_buy_then_sell_conserves_value_modulo_fees() -> None:
    state = PortfolioState.initial(Decimal("1000"))
    state = state.apply_fill(honest("AAPL", Side.BUY, "5", "100", "1"))
    assert state.cash == Decimal("499")  # 1000 - 500 - 1 fee
    assert state.position("AAPL") == Decimal("5")
    # Mark-to-market at the buy price: equity dropped by exactly the fee.
    assert state.equity({"AAPL": Decimal("100")}) == Decimal("999")

    state = state.apply_fill(honest("AAPL", Side.SELL, "5", "100", "1"))
    assert state.cash == Decimal("998")  # 499 + 500 - 1 fee
    assert "AAPL" not in state.positions  # zeroed positions are pruned


def test_apply_fill_rejects_tampered_cash_delta() -> None:
    tampered = Fill(
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("5"),
        price=Decimal("100"),
        fees=Decimal("0"),
        cash_delta=Decimal("0"),  # should be -500
        position_delta=Decimal("5"),
    )
    with pytest.raises(InvariantViolation, match="not conservative"):
        PortfolioState.initial(Decimal("1000")).apply_fill(tampered)


def test_check_fill_conservation_verdicts() -> None:
    good = honest("AAPL", Side.BUY, "5", "100", "1")
    assert not check_fill_conservation(good).is_veto
    bad = Fill(
        "AAPL",
        Side.SELL,
        Decimal("5"),
        Decimal("100"),
        Decimal("0"),
        cash_delta=Decimal("99999"),
        position_delta=Decimal("-5"),
    )
    verdict = check_fill_conservation(bad)
    assert verdict.is_veto
    assert verdict.evidence["expected_cash_delta"] == "500"


def test_mark_only_raises_high_water_mark() -> None:
    state = PortfolioState.initial(Decimal("1000")).apply_fill(honest("AAPL", Side.BUY, "1", "100"))
    up = state.mark({"AAPL": Decimal("150")})  # equity 1050 > hwm 1000
    assert up.high_water_mark == Decimal("1050")
    down = up.mark({"AAPL": Decimal("50")})  # equity 950 < hwm 1050
    assert down.high_water_mark == Decimal("1050")  # unchanged


def test_equity_requires_price_for_held_symbol() -> None:
    state = PortfolioState.initial(Decimal("1000")).apply_fill(honest("AAPL", Side.BUY, "1", "100"))
    with pytest.raises(ValueError, match="no price for held symbol"):
        state.equity({"MSFT": Decimal("10")})


def test_replay_is_deterministic_and_idempotent() -> None:
    """Invariant 7: re-applying the same fill sequence yields the identical state."""
    initial = PortfolioState.initial(Decimal("10000"))
    seq = [
        honest("AAPL", Side.BUY, "10", "100", "1"),
        honest("MSFT", Side.BUY, "5", "200", "2"),
        honest("AAPL", Side.SELL, "4", "110", "1"),
    ]
    first = replay_fills(initial, seq)
    second = replay_fills(initial, seq)
    assert first == second
    assert first.cash == second.cash and first.positions == second.positions


@given(sides, qtys, prices, fees)
def test_conservation_law_holds_exactly(
    side: Side, qty: Decimal, price: Decimal, fee: Decimal
) -> None:
    """Property: cash_delta + price*position_delta == -fees, exactly (no residual)."""
    fill = Fill.honest("AAPL", side, qty, price, fee)
    verify_fill_consistency(fill)  # must not raise
    assert fill.cash_delta + fill.price * fill.position_delta == -fee


@given(st.lists(st.tuples(sides, qtys, prices, fees), min_size=0, max_size=12))
def test_random_fill_sequences_conserve_cash(
    rows: list[tuple[Side, Decimal, Decimal, Decimal]],
) -> None:
    """Property: total equity change equals -Σfees when marked at fill prices."""
    initial = PortfolioState.initial(Decimal("1000000"))
    fills = [Fill.honest("AAPL", s, q, p, f) for (s, q, p, f) in rows]
    final = replay_fills(initial, fills)
    # The fold is exact: final cash and position equal the sums of the deltas,
    # with no rounding residual anywhere in the accounting path.
    total_cash_delta = sum((f.cash_delta for f in fills), Decimal(0))
    total_position = sum((f.position_delta for f in fills), Decimal(0))
    assert final.cash == initial.cash + total_cash_delta
    assert final.position("AAPL") == total_position
