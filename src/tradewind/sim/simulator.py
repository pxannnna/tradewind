"""The bar-replay simulator: agents propose, the harness vets and fills.

Contract and timing model (stated once, precisely):

* The clock advances one daily bar at a time; there is no wall-clock or global
  RNG use anywhere in this module.
* On each trading day the engine (1) executes orders queued from the *previous*
  day at **today's open**, (2) marks the portfolio to **today's close**, then
  (3) invokes the agent, which may propose orders using only data up to and
  including today. Orders are queued for the next day's open.
* Every queued order is run through the full invariant pipeline at execution
  time, against a :class:`MarketContext` whose ``last_price`` is the actual
  open fill price. So the invariants vet the *real* execution economics, and
  the resulting portfolio can never violate an invariant. Admitted orders fill
  at ``open × (1 ± slippage)`` with fees; blocked orders are dropped and a
  ``violation`` event records why.
* Orders proposed on the final bar cannot execute (no next open) and are
  dropped; this is documented, not silently ignored.

When a :class:`~tradewind.trace.writer.TraceWriter` is supplied, the run emits
``proposed_action`` → ``invariant_check`` → (``violation`` | ``fill``) events
with causal ``parent_seq`` links, so a report can trace any fill back to the
decision that produced it. (Per-bar market data is described by the run config,
not emitted as ``data_read`` events — those belong to the boundary-based
adapter path in Phase 6.)
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal

from tradewind.invariants.checks import default_invariants
from tradewind.invariants.domain import Fill, MarketContext, MarketModel, ProposedAction, RiskConfig
from tradewind.invariants.engine import InvariantEngine, VetoPolicy, record_evaluation
from tradewind.invariants.portfolio import PortfolioState
from tradewind.sim.bars import PriceData
from tradewind.trace.writer import TraceWriter

#: Fixed, deterministic intraday stamps (UTC) for open and close marks. These
#: exist only to give events a virtual timestamp; nothing reads the wall clock.
OPEN_TIME = time(14, 30, tzinfo=UTC)
CLOSE_TIME = time(21, 0, tzinfo=UTC)


@dataclass(frozen=True)
class DecisionContext:
    """What an agent may see when proposing orders on a given day.

    Exposes only data up to and including ``day`` — the accessors clamp to the
    current index, so an agent cannot peek at future bars.
    """

    day: date
    index: int
    portfolio: PortfolioState
    prices: PriceData

    def today_close(self, symbol: str) -> Decimal | None:
        """Today's closing price for ``symbol`` (or ``None`` if not present)."""
        bar = self.prices.bar_on(symbol, self.day)
        return None if bar is None else bar.close

    def recent_closes(self, symbol: str, lookback: int) -> list[Decimal]:
        """Up to ``lookback`` closes ending today (oldest first); no future data."""
        series = self.prices.bars.get(symbol, ())
        window = [b.close for b in series if b.day <= self.day]
        return window[-lookback:] if lookback > 0 else []


#: An agent maps a decision context to the orders it wants filled next open.
Agent = Callable[[DecisionContext], Sequence[ProposedAction]]


@dataclass(frozen=True)
class SimConfig:
    """Everything the simulator needs beyond the price data itself."""

    initial_cash: Decimal
    universe: frozenset[str]
    risk: RiskConfig
    model: MarketModel = field(default_factory=MarketModel)
    veto_policy: VetoPolicy = VetoPolicy.SUPPRESS


@dataclass(frozen=True)
class EquityPoint:
    """A single mark-to-market point on the equity curve."""

    day: date
    equity: Decimal


@dataclass(frozen=True)
class SimulationResult:
    """The outcome of a run: final state, equity curve, and decision tallies."""

    final_state: PortfolioState
    equity_curve: tuple[EquityPoint, ...]
    fills: tuple[Fill, ...]
    proposed: int
    admitted: int
    violations: int

    @property
    def final_equity(self) -> Decimal:
        """Mark-to-market equity at the last bar's close."""
        return self.equity_curve[-1].equity if self.equity_curve else self.final_state.cash


class Simulator:
    """Replays bundled price data, driving an agent under the invariant engine."""

    def __init__(self, prices: PriceData, config: SimConfig) -> None:
        missing = config.universe - prices.symbols
        if missing:
            raise ValueError(f"universe symbols missing from price data: {sorted(missing)}")
        self._prices = prices
        self._config = config
        self._engine = InvariantEngine(default_invariants(config.risk), policy=config.veto_policy)

    def run(self, agent: Agent, writer: TraceWriter | None = None) -> SimulationResult:
        """Run ``agent`` over every bar; optionally record the decision trace."""
        state = PortfolioState.initial(self._config.initial_cash)
        universe = self._config.universe
        pending: list[tuple[ProposedAction, date]] = []
        curve: list[EquityPoint] = []
        fills: list[Fill] = []
        proposed = admitted = violations = 0

        for index, day in enumerate(self._prices.trading_days):
            # (1) Execute yesterday's orders at today's open.
            opens = self._prices.opens_on(day, universe)
            exec_ctx = MarketContext(
                now=datetime.combine(day, OPEN_TIME),
                last_price=opens,
                tradeable_universe=universe,
                model=self._config.model,
            )
            for action, decided_on in pending:
                proposed += 1
                result = self._engine.evaluate(state, action, exec_ctx)
                parent = None
                if writer is not None:
                    payload = {
                        **action.to_payload(),
                        "decided_on": decided_on.isoformat(),
                        "executed_on": day.isoformat(),
                    }
                    action_event = writer.append("proposed_action", payload)
                    check_events = record_evaluation(writer, result, parent_seq=action_event.seq)
                    parent = check_events[0].seq
                if result.blocked:
                    violations += len(result.vetoes)
                    continue
                fill = exec_ctx.project_fill(action)
                if fill is None:  # pragma: no cover - engine admits only projectable actions
                    continue
                state = state.record_order(exec_ctx.now).apply_fill(fill)
                fills.append(fill)
                admitted += 1
                if writer is not None:
                    writer.append("fill", fill.to_payload(), parent_seq=parent)

            # (2) Mark to today's close.
            closes = self._prices.closes_on(day, universe)
            state = state.mark(closes)
            curve.append(EquityPoint(day=day, equity=state.equity(closes)))

            # (3) Agent proposes, using only data through today; queue for next open.
            decision_ctx = DecisionContext(
                day=day, index=index, portfolio=state, prices=self._prices
            )
            pending = [(action, day) for action in agent(decision_ctx)]

        # Orders from the final bar have no next open and are dropped by design.
        return SimulationResult(
            final_state=state,
            equity_curve=tuple(curve),
            fills=tuple(fills),
            proposed=proposed,
            admitted=admitted,
            violations=violations,
        )
