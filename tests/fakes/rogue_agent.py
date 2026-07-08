"""A deliberately hostile fake agent for the containment acceptance test.

Contract of the test that uses it (spec Phase 2): every action this agent
proposes is invalid or unsafe, so the invariant engine must block *all* of
them and zero invalid fills may reach the portfolio.
"""

from decimal import Decimal

from tradewind.invariants.domain import ProposedAction, Side


def hostile_actions() -> list[ProposedAction]:
    """Return a battery of orders that must every one be vetoed.

    Covers the classes the spec calls out — oversized orders, negative and
    zero quantities, unknown symbols — plus non-finite quantity and a bad
    limit price for good measure.
    """
    return [
        # Oversized order (far beyond any sane position/exposure cap).
        ProposedAction("AAPL", Side.BUY, Decimal("100000000")),
        # Negative quantity.
        ProposedAction("AAPL", Side.BUY, Decimal("-10")),
        # Zero quantity.
        ProposedAction("AAPL", Side.SELL, Decimal("0")),
        # Symbol outside the tradeable universe (hallucinated instrument).
        ProposedAction("DOGE-TO-THE-MOON", Side.BUY, Decimal("1")),
        # Non-finite quantity.
        ProposedAction("AAPL", Side.BUY, Decimal("NaN")),
        # Negative limit price.
        ProposedAction("MSFT", Side.BUY, Decimal("1"), limit_price=Decimal("-5")),
        # A buy that would blow through available cash (naive apply → negative cash).
        ProposedAction("MSFT", Side.BUY, Decimal("100000")),
    ]
