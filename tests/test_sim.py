"""Simulator: golden-file exactness, trace emission, veto containment, properties."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from tests.helpers import make_header
from tradewind.invariants.domain import Fill, MarketModel, ProposedAction, RiskConfig, Side
from tradewind.invariants.portfolio import PortfolioState, replay_fills
from tradewind.sim.agents import BuyAndHold, ScriptedAgent, SmaCrossover
from tradewind.sim.bars import load_price_data
from tradewind.sim.simulator import SimConfig, Simulator
from tradewind.trace.reader import read_trace
from tradewind.trace.replay import replay_trace_file
from tradewind.trace.writer import TraceWriter

DATA = Path(__file__).resolve().parent.parent / "benchmarks" / "data"
GOLDEN = DATA / "golden"


def _generous_risk(max_pos: str = "1000000") -> RiskConfig:
    return RiskConfig(
        max_position_per_symbol=Decimal(max_pos),
        max_gross_exposure=Decimal("100000000"),
        max_drawdown=Decimal("0.50"),
        price_sanity_pct=Decimal("0.10"),
        max_orders_per_window=1000,
        rate_window_seconds=86400,
    )


def _golden_config(max_pos: str = "1000000") -> SimConfig:
    return SimConfig(
        initial_cash=Decimal("10000"),
        universe=frozenset({"AAPL"}),
        risk=_generous_risk(max_pos),
        model=MarketModel(),  # zero fees, zero slippage → exact round numbers
    )


def test_golden_scripted_run() -> None:
    """Exact final state for a hand-verifiable scripted run (no fees/slippage).

    Day 01-02 close: propose BUY 10. Day 01-03 open (110): fill → cash 8900,
    pos 10. Day 01-03 close: propose SELL 10. Day 01-04 open (120): fill →
    cash 10100, pos 0. Nothing more executes.
    """
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    agent = ScriptedAgent(
        {
            date(2024, 1, 2): [ProposedAction("AAPL", Side.BUY, Decimal("10"))],
            date(2024, 1, 3): [ProposedAction("AAPL", Side.SELL, Decimal("10"))],
        }
    )
    result = Simulator(prices, _golden_config()).run(agent)

    assert result.final_state.cash == Decimal("10100")
    assert result.final_state.positions == {}
    assert result.proposed == 2
    assert result.admitted == 2
    assert result.violations == 0
    assert [p.equity for p in result.equity_curve] == [
        Decimal("10000"),  # 01-02 all cash
        Decimal("10000"),  # 01-03 8900 cash + 10*110
        Decimal("10100"),  # 01-04 all cash after sell
        Decimal("10100"),  # 01-05 flat
    ]
    assert result.final_equity == Decimal("10100")


def test_buy_and_hold_opens_one_position() -> None:
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    result = Simulator(prices, _golden_config()).run(BuyAndHold("AAPL", Decimal("10")))
    # Buys 10 at 01-03 open (110) and holds; ends holding 10 shares.
    assert result.admitted == 1
    assert result.final_state.position("AAPL") == Decimal("10")
    assert result.final_equity == Decimal("8900") + Decimal("10") * Decimal("130")


def test_run_is_deterministic() -> None:
    prices = load_price_data(DATA, frozenset({"AAPL"}))
    config = SimConfig(
        initial_cash=Decimal("100000"),
        universe=frozenset({"AAPL"}),
        risk=_generous_risk(),
        model=MarketModel(fee_bps=Decimal("2"), slippage_bps=Decimal("5")),
    )
    agent = SmaCrossover("AAPL", Decimal("100"))
    a = Simulator(prices, config).run(agent)
    b = Simulator(prices, config).run(agent)
    assert a.final_state == b.final_state
    assert a.final_equity == b.final_equity


def test_fills_execute_at_next_open_with_slippage_and_fees() -> None:
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    config = SimConfig(
        initial_cash=Decimal("10000"),
        universe=frozenset({"AAPL"}),
        risk=_generous_risk(),
        model=MarketModel(fee_bps=Decimal("10"), slippage_bps=Decimal("50")),
    )
    agent = ScriptedAgent({date(2024, 1, 2): [ProposedAction("AAPL", Side.BUY, Decimal("10"))]})
    result = Simulator(prices, config).run(agent)
    fill = result.fills[0]
    # Buy fills at next open (110) + 50 bps slippage; fee = notional * 10 bps.
    assert fill.price == Decimal("110.00") * (Decimal(1) + Decimal("0.005"))
    assert fill.fees == fill.price * fill.quantity * Decimal("0.001")


def test_oversized_order_is_vetoed_and_portfolio_protected() -> None:
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    # Cap position at 5 shares; agent tries to buy 10 → veto at execution.
    agent = ScriptedAgent({date(2024, 1, 2): [ProposedAction("AAPL", Side.BUY, Decimal("10"))]})
    result = Simulator(prices, _golden_config(max_pos="5")).run(agent)
    assert result.admitted == 0
    assert result.violations >= 1
    assert result.final_state.positions == {}
    assert result.final_state.cash == Decimal("10000")  # untouched


def test_trace_emission_verifies_and_replays_byte_identically(tmp_path: Path) -> None:
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    agent = ScriptedAgent(
        {
            date(2024, 1, 2): [ProposedAction("AAPL", Side.BUY, Decimal("10"))],
            date(2024, 1, 3): [ProposedAction("AAPL", Side.SELL, Decimal("10"))],
        }
    )
    trace = tmp_path / "sim.jsonl"
    with TraceWriter(trace, make_header()) as writer:
        Simulator(prices, _golden_config()).run(agent, writer=writer)

    _, events = read_trace(trace)
    kinds = [e.event_type for e in events]
    assert "proposed_action" in kinds and "invariant_check" in kinds and "fill" in kinds
    # Every fill links back through an invariant_check to a proposed_action.
    assert replay_trace_file(trace).byte_identical


def test_universe_must_be_in_price_data() -> None:
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    config = SimConfig(
        initial_cash=Decimal("1000"),
        universe=frozenset({"AAPL", "TSLA"}),
        risk=_generous_risk(),
    )
    import pytest

    with pytest.raises(ValueError, match="missing from price data"):
        Simulator(prices, config)


def test_decision_context_has_no_lookahead() -> None:
    """An agent only ever sees closes up to and including the current day."""
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    seen: list[int] = []

    def spy_agent(ctx):  # type: ignore[no-untyped-def]
        closes = ctx.recent_closes("AAPL", 999)
        seen.append(len(closes))
        # Never more history than bars elapsed so far (index + 1).
        assert len(closes) == ctx.index + 1
        # today_close is the last visible close, and unknown symbols read None.
        assert ctx.today_close("AAPL") == closes[-1]
        assert ctx.today_close("NOPE") is None
        return []

    Simulator(prices, _golden_config()).run(spy_agent)
    assert seen == [1, 2, 3, 4]


# --- Properties ------------------------------------------------------------

_fill_rows = st.lists(
    st.tuples(
        st.sampled_from([Side.BUY, Side.SELL]),
        st.decimals(
            min_value="0.001", max_value="1000", allow_nan=False, allow_infinity=False, places=3
        ),
        st.decimals(
            min_value="0.01", max_value="5000", allow_nan=False, allow_infinity=False, places=2
        ),
    ),
    min_size=0,
    max_size=10,
)


@given(_fill_rows, st.randoms(use_true_random=False))
def test_accounting_fold_is_permutation_invariant(
    rows: list[tuple[Side, Decimal, Decimal]], rng: object
) -> None:
    """Pure accounting commutes: reordering fills yields the same cash/positions.

    NOTE: this holds for the *accounting fold* (Decimal addition is
    commutative), not for the gated pipeline — the drawdown and rate-limit
    checks are deliberately order-dependent, so admission is not commutative.
    """
    import random as _random  # local: property test, not core code

    assert isinstance(rng, _random.Random)
    fills = [Fill.honest("AAPL", s, q, p, Decimal("0")) for (s, q, p) in rows]
    ordered = replay_fills(PortfolioState.initial(Decimal("1000000000")), fills)
    shuffled = list(fills)
    rng.shuffle(shuffled)
    permuted = replay_fills(PortfolioState.initial(Decimal("1000000000")), shuffled)
    assert ordered.cash == permuted.cash
    assert ordered.positions == permuted.positions
