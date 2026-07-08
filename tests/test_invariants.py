"""Per-invariant pass/veto unit tests plus Hypothesis properties."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from tradewind.invariants.checks import (
    CashConservation,
    DrawdownBreaker,
    NoNegativeCash,
    OrderValidity,
    PositionLimit,
    RateLimit,
)
from tradewind.invariants.domain import (
    MarketContext,
    MarketModel,
    ProposedAction,
    Side,
)
from tradewind.invariants.portfolio import PortfolioState

NOW = datetime(2024, 1, 2, tzinfo=UTC)
UNIVERSE = frozenset({"AAPL", "MSFT"})


def ctx(**overrides: object) -> MarketContext:
    fields: dict[str, object] = {
        "now": NOW,
        "last_price": {"AAPL": Decimal("100"), "MSFT": Decimal("200")},
        "tradeable_universe": UNIVERSE,
        "model": MarketModel(),
    }
    fields.update(overrides)
    return MarketContext(**fields)  # type: ignore[arg-type]


def buy(symbol: str = "AAPL", qty: str = "1") -> ProposedAction:
    return ProposedAction(symbol, Side.BUY, Decimal(qty))


# --- OrderValidity ---------------------------------------------------------


def test_order_validity_passes_well_formed() -> None:
    v = OrderValidity(price_sanity_pct=Decimal("0.1")).check(
        PortfolioState.initial(Decimal("1000")), buy(), ctx()
    )
    assert not v.is_veto


def test_order_validity_vetoes_unknown_symbol() -> None:
    v = OrderValidity(price_sanity_pct=Decimal("0.1")).check(
        PortfolioState.initial(Decimal("1000")), buy("NOPE"), ctx()
    )
    assert v.is_veto and "universe" in v.reason


def test_order_validity_vetoes_non_positive_and_non_finite_qty() -> None:
    inv = OrderValidity(price_sanity_pct=Decimal("0.1"))
    state = PortfolioState.initial(Decimal("1000"))
    assert inv.check(state, buy(qty="0"), ctx()).is_veto
    assert inv.check(state, buy(qty="-1"), ctx()).is_veto
    assert inv.check(state, ProposedAction("AAPL", Side.BUY, Decimal("NaN")), ctx()).is_veto


def test_order_validity_price_sanity_band() -> None:
    # 10% slippage but only a 5% sanity band → veto.
    market = ctx(model=MarketModel(slippage_bps=Decimal("1000")))
    v = OrderValidity(price_sanity_pct=Decimal("0.05")).check(
        PortfolioState.initial(Decimal("100000")), buy(), market
    )
    assert v.is_veto and "sanity band" in v.reason


# --- CashConservation ------------------------------------------------------


def test_cash_conservation_passes_projected_fill() -> None:
    v = CashConservation().check(PortfolioState.initial(Decimal("1000")), buy(), ctx())
    assert not v.is_veto


def test_cash_conservation_passes_unprojectable() -> None:
    v = CashConservation().check(PortfolioState.initial(Decimal("1000")), buy("NOPE"), ctx())
    assert not v.is_veto  # OrderValidity owns that veto


# --- NoNegativeCash --------------------------------------------------------


def test_no_negative_cash_pass_and_veto() -> None:
    inv = NoNegativeCash(margin_limit=Decimal("0"))
    poor = PortfolioState.initial(Decimal("50"))
    assert inv.check(poor, buy(qty="1"), ctx()).is_veto  # needs 100, only 50
    rich = PortfolioState.initial(Decimal("1000"))
    assert not inv.check(rich, buy(qty="1"), ctx()).is_veto


def test_margin_limit_allows_configured_overdraft() -> None:
    inv = NoNegativeCash(margin_limit=Decimal("100"))
    state = PortfolioState.initial(Decimal("50"))  # buy of 100 → cash -50, within -100 floor
    assert not inv.check(state, buy(qty="1"), ctx()).is_veto


# --- PositionLimit ---------------------------------------------------------


def test_position_limit_per_symbol_veto() -> None:
    inv = PositionLimit(
        max_position_per_symbol=Decimal("10"), max_gross_exposure=Decimal("1000000")
    )
    state = PortfolioState.initial(Decimal("1000000"))
    assert inv.check(state, buy(qty="11"), ctx()).is_veto
    assert not inv.check(state, buy(qty="10"), ctx()).is_veto


def test_position_limit_gross_exposure_veto() -> None:
    inv = PositionLimit(
        max_position_per_symbol=Decimal("1000000"), max_gross_exposure=Decimal("500")
    )
    state = PortfolioState.initial(Decimal("1000000"))
    # 6 shares * 100 = 600 gross > 500 cap.
    assert inv.check(state, buy(qty="6"), ctx()).is_veto


# --- DrawdownBreaker -------------------------------------------------------


def test_drawdown_breaker_halts_when_below_threshold() -> None:
    inv = DrawdownBreaker(max_drawdown=Decimal("0.20"))
    # hwm 1000, equity 700 (all cash) → below 800 threshold → veto everything.
    state = PortfolioState(cash=Decimal("700"), positions={}, high_water_mark=Decimal("1000"))
    assert inv.check(state, buy(), ctx()).is_veto
    healthy = PortfolioState(cash=Decimal("900"), positions={}, high_water_mark=Decimal("1000"))
    assert not inv.check(healthy, buy(), ctx()).is_veto


# --- RateLimit -------------------------------------------------------------


def test_rate_limit_pass_then_veto() -> None:
    inv = RateLimit(max_orders_per_window=2, rate_window=timedelta(minutes=5))
    times = (NOW - timedelta(minutes=1), NOW - timedelta(minutes=2))
    state = PortfolioState(cash=Decimal("1000"), positions={}, recent_order_times=times)
    assert inv.check(state, buy(), ctx()).is_veto  # 2 already in window, at cap
    old = (NOW - timedelta(hours=1), NOW - timedelta(hours=2))
    stale = PortfolioState(cash=Decimal("1000"), positions={}, recent_order_times=old)
    assert not inv.check(stale, buy(), ctx()).is_veto  # both outside the 5-min window


# --- Properties ------------------------------------------------------------


@given(
    st.decimals(
        min_value="0.001", max_value="1000000", allow_nan=False, allow_infinity=False, places=3
    )
)
def test_property_position_limit_admits_only_within_cap(qty: Decimal) -> None:
    cap = Decimal("100")
    inv = PositionLimit(max_position_per_symbol=cap, max_gross_exposure=Decimal("1e18"))
    state = PortfolioState.initial(Decimal("1e12"))
    verdict = inv.check(state, ProposedAction("AAPL", Side.BUY, qty), ctx())
    if not verdict.is_veto:
        assert qty <= cap  # anything admitted respects the cap


@given(st.text(min_size=1, max_size=8))
def test_property_unknown_symbols_always_vetoed(symbol: str) -> None:
    inv = OrderValidity(price_sanity_pct=Decimal("1"))
    state = PortfolioState.initial(Decimal("1000"))
    verdict = inv.check(state, ProposedAction(symbol, Side.BUY, Decimal("1")), ctx())
    if symbol not in UNIVERSE:
        assert verdict.is_veto
