"""Portfolio state and the conservation-enforcing fill application.

Contract: :class:`PortfolioState` is a frozen, exact-Decimal snapshot. Every
mutator returns a *new* state (no in-place change), so replay is a pure fold
over fills. Money arithmetic performs no rounding (see :mod:`tradewind.money`),
so the conservation identity holds exactly.

Invariant 1 (cash conservation) is enforced as a hard post-condition here:
:func:`verify_fill_consistency` recomputes a fill's canonical economics and
raises :class:`~tradewind.errors.InvariantViolation` if the claimed
``cash_delta``/``position_delta`` disagree — a fill cannot create or destroy
cash by being applied.

Invariant 7 (idempotent replay) is a property of :func:`replay_fills`: it is a
deterministic fold, so re-applying the same fill sequence to the same initial
state always yields the identical final state.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from tradewind.errors import InvariantViolation
from tradewind.invariants.domain import Fill
from tradewind.invariants.verdict import Verdict
from tradewind.money import ZERO

CASH_CONSERVATION = "cash_conservation"


def verify_fill_consistency(fill: Fill) -> None:
    """Raise :class:`InvariantViolation` if a fill's economics are self-inconsistent.

    Formal property enforced::

        position_delta == sign(side) · quantity
        cash_delta     == -sign(side) · price · quantity - fees

    which together imply the conservation law ``cash_delta + price ·
    position_delta == -fees`` exactly. A fill that violates this would create
    or destroy value when applied and must never reach the portfolio.
    """
    sign = fill.side.sign()
    expected_position = sign * fill.quantity
    expected_cash = -sign * fill.price * fill.quantity - fill.fees
    if fill.position_delta != expected_position or fill.cash_delta != expected_cash:
        raise InvariantViolation(
            f"fill for {fill.symbol} is not conservative: claimed "
            f"cash_delta={fill.cash_delta}, position_delta={fill.position_delta}; "
            f"canonical cash_delta={expected_cash}, position_delta={expected_position} "
            f"(side={fill.side.value}, price={fill.price}, qty={fill.quantity}, "
            f"fees={fill.fees})"
        )


def check_fill_conservation(fill: Fill) -> Verdict:
    """Verdict form of :func:`verify_fill_consistency` (VETO instead of raise)."""
    try:
        verify_fill_consistency(fill)
    except InvariantViolation as exc:
        sign = fill.side.sign()
        return Verdict.veto(
            CASH_CONSERVATION,
            str(exc),
            symbol=fill.symbol,
            claimed_cash_delta=str(fill.cash_delta),
            expected_cash_delta=str(-sign * fill.price * fill.quantity - fill.fees),
        )
    return Verdict.passed(CASH_CONSERVATION)


@dataclass(frozen=True)
class PortfolioState:
    """Cash, signed positions, drawdown high-water mark, and recent order times."""

    cash: Decimal
    positions: Mapping[str, Decimal] = field(default_factory=dict)
    high_water_mark: Decimal = ZERO
    recent_order_times: tuple[datetime, ...] = ()

    @classmethod
    def initial(cls, cash: Decimal) -> "PortfolioState":
        """Open a portfolio: all cash, no positions, high-water mark = cash."""
        return cls(cash=cash, positions={}, high_water_mark=cash, recent_order_times=())

    def position(self, symbol: str) -> Decimal:
        """Signed position in ``symbol`` (0 if none held)."""
        return self.positions.get(symbol, ZERO)

    def equity(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Mark-to-market equity: ``cash + Σ position_i × price_i``.

        Every held symbol must be priced; a missing quote is a data error and
        raises loudly rather than silently marking the position at zero.
        """
        total = self.cash
        for symbol, qty in self.positions.items():
            price = prices.get(symbol)
            if price is None:
                raise ValueError(f"cannot mark equity: no price for held symbol {symbol!r}")
            total += qty * price
        return total

    def gross_exposure(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Gross exposure: ``Σ |position_i × price_i|`` (both long and short)."""
        total = ZERO
        for symbol, qty in self.positions.items():
            price = prices.get(symbol)
            if price is None:
                raise ValueError(f"cannot measure exposure: no price for {symbol!r}")
            total += abs(qty * price)
        return total

    def apply_fill(self, fill: Fill) -> "PortfolioState":
        """Return a new state with ``fill`` applied; enforce conservation first.

        Raises :class:`InvariantViolation` if the fill is not conservative, so
        an inconsistent fill can never mutate the portfolio.
        """
        verify_fill_consistency(fill)
        new_positions = dict(self.positions)
        updated = new_positions.get(fill.symbol, ZERO) + fill.position_delta
        if updated == ZERO:
            new_positions.pop(fill.symbol, None)
        else:
            new_positions[fill.symbol] = updated
        return replace(self, cash=self.cash + fill.cash_delta, positions=new_positions)

    def mark(self, prices: Mapping[str, Decimal]) -> "PortfolioState":
        """Return a new state with the high-water mark raised to current equity."""
        current = self.equity(prices)
        if current <= self.high_water_mark:
            return self
        return replace(self, high_water_mark=current)

    def record_order(self, when: datetime) -> "PortfolioState":
        """Return a new state noting an admitted order at virtual time ``when``."""
        return replace(self, recent_order_times=(*self.recent_order_times, when))

    def orders_within(self, window: timedelta, now: datetime) -> int:
        """Count recorded orders in the trailing ``window`` ending at ``now``."""
        start = now - window
        return sum(1 for t in self.recent_order_times if start < t <= now)


def replay_fills(initial: PortfolioState, fills: Iterable[Fill]) -> PortfolioState:
    """Fold a fill sequence onto a starting state (deterministic; invariant 7).

    Because :meth:`PortfolioState.apply_fill` is pure and rounding-free, this
    fold is referentially transparent: the same ``initial`` and ``fills``
    always produce the identical final state, and each fill is conservation-
    checked on the way in.
    """
    state = initial
    for fill in fills:
        state = state.apply_fill(fill)
    return state
