"""Acceptance (Phase 2): a hostile agent is fully contained — zero bad fills.

Drives every action from ``tests/fakes/rogue_agent.py`` through the engine and
portfolio and asserts (a) every hostile action is blocked and (b) the portfolio
is byte-for-byte unchanged from its opening state.
"""

from datetime import UTC, datetime
from decimal import Decimal

from tests.fakes.rogue_agent import hostile_actions
from tradewind.invariants.checks import default_invariants
from tradewind.invariants.domain import MarketContext, RiskConfig
from tradewind.invariants.engine import InvariantEngine, VetoPolicy
from tradewind.invariants.portfolio import PortfolioState

NOW = datetime(2024, 1, 2, tzinfo=UTC)
UNIVERSE = frozenset({"AAPL", "MSFT"})


def _risk() -> RiskConfig:
    return RiskConfig(
        max_position_per_symbol=Decimal("100"),
        max_gross_exposure=Decimal("100000"),
        max_drawdown=Decimal("0.20"),
        price_sanity_pct=Decimal("0.10"),
        max_orders_per_window=1000,  # deliberately loose: containment must not rely on it
        rate_window_seconds=3600,
    )


def _market() -> MarketContext:
    return MarketContext(
        now=NOW,
        last_price={"AAPL": Decimal("100"), "MSFT": Decimal("200")},
        tradeable_universe=UNIVERSE,
    )


def test_every_hostile_action_is_blocked_and_portfolio_untouched() -> None:
    engine = InvariantEngine(default_invariants(_risk()), policy=VetoPolicy.SUPPRESS)
    opening = PortfolioState.initial(Decimal("10000"))
    state = opening
    applied_fills = 0

    for action in hostile_actions():
        result = engine.evaluate(state, action, _market())
        assert result.blocked, f"hostile action slipped through: {action}"
        if not result.blocked:  # pragma: no cover - defensive: never apply a blocked action
            fill = _market().project_fill(action)
            if fill is not None:
                state = state.apply_fill(fill)
                applied_fills += 1

    assert applied_fills == 0
    assert state == opening  # not one fill mutated the portfolio
