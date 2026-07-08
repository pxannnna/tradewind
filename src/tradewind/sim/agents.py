"""Reusable scripted agents (deterministic, no LLM, no randomness).

These drive the golden-file test, the benchmark, and examples. Each is a pure
function of the :class:`DecisionContext`, so a run over fixed data is exactly
reproducible.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tradewind.invariants.domain import ProposedAction, Side
from tradewind.sim.simulator import DecisionContext


@dataclass(frozen=True)
class BuyAndHold:
    """Buy a fixed quantity on the first decision, then hold forever."""

    symbol: str
    quantity: Decimal

    def __call__(self, ctx: DecisionContext) -> Sequence[ProposedAction]:
        """Propose the single opening buy on day 0; nothing thereafter."""
        if ctx.index == 0:
            return [ProposedAction(self.symbol, Side.BUY, self.quantity)]
        return []


@dataclass(frozen=True)
class ScriptedAgent:
    """Propose a fixed set of orders on named decision days (for exact tests)."""

    schedule: Mapping[date, Sequence[ProposedAction]]

    def __call__(self, ctx: DecisionContext) -> Sequence[ProposedAction]:
        """Return the orders scheduled for today's decision, if any."""
        return list(self.schedule.get(ctx.day, ()))


@dataclass(frozen=True)
class SmaCrossover:
    """A tiny long-only momentum agent: hold when fast SMA > slow SMA, else flat.

    Deterministic and intentionally naive — it exists to exercise the harness
    over realistic-looking data, not to make money.
    """

    symbol: str
    quantity: Decimal
    fast: int = 5
    slow: int = 20

    def __call__(self, ctx: DecisionContext) -> Sequence[ProposedAction]:
        """Buy the target size when fast SMA crosses above slow; sell when below."""
        closes = ctx.recent_closes(self.symbol, self.slow)
        if len(closes) < self.slow:
            return []
        fast_avg = sum(closes[-self.fast :], Decimal(0)) / self.fast
        slow_avg = sum(closes, Decimal(0)) / self.slow
        held = ctx.portfolio.position(self.symbol)
        if fast_avg > slow_avg and held <= Decimal(0):
            return [ProposedAction(self.symbol, Side.BUY, self.quantity)]
        if fast_avg <= slow_avg and held > Decimal(0):
            return [ProposedAction(self.symbol, Side.SELL, held)]
        return []
