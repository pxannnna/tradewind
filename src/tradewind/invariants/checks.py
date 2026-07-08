"""The invariant pipeline: six pure, deterministic checks over a proposed action.

Contract: each invariant is a frozen, configured object exposing
``check(state, action, ctx) -> Verdict`` (the signature the spec mandates). No
check performs I/O, imports any LLM library, reads the wall clock, or touches
global RNG. Economic checks obtain the paper fill via
:meth:`MarketContext.project_fill`; when an action is unprojectable they return
``PASS`` and defer the veto to :class:`OrderValidity`, so evidence stays clean.

Each class docstring states the formal property it enforces.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradewind.invariants.domain import MarketContext, ProposedAction, RiskConfig
from tradewind.invariants.portfolio import PortfolioState, check_fill_conservation
from tradewind.invariants.verdict import Verdict
from tradewind.money import ZERO


@runtime_checkable
class Invariant(Protocol):
    """A configured, pure risk check."""

    @property
    def name(self) -> str:
        """Stable identifier used in verdicts and violation events."""
        ...

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Rule on ``action`` given portfolio ``state`` and market ``ctx``."""
        ...


@dataclass(frozen=True)
class OrderValidity:
    """Reject malformed orders.

    Formal property: an admitted action has ``side ∈ {buy, sell}``,
    ``symbol ∈ universe``, a finite ``quantity > 0``, a positive ``limit_price``
    if one is given, and a projected fill price within ``price_sanity_pct`` of
    the last quote. Anything else is vetoed here so downstream economic checks
    can assume a projectable fill.
    """

    price_sanity_pct: Decimal
    name: str = "order_validity"

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Veto ill-formed orders; see the class docstring for the property.

        ``side`` is a :class:`Side` enum by construction (``from_payload`` raises
        on an unknown side before a ``ProposedAction`` can exist), so this check
        need not re-validate it.
        """
        if action.symbol not in ctx.tradeable_universe:
            return Verdict.veto(
                self.name,
                f"symbol {action.symbol!r} is not in the tradeable universe",
                symbol=action.symbol,
            )
        if not action.quantity.is_finite():
            return Verdict.veto(
                self.name,
                "quantity is not finite",
                symbol=action.symbol,
                quantity=str(action.quantity),
            )
        if action.quantity <= ZERO:
            return Verdict.veto(
                self.name,
                "quantity must be strictly positive",
                symbol=action.symbol,
                quantity=str(action.quantity),
            )
        if action.limit_price is not None and action.limit_price <= ZERO:
            return Verdict.veto(
                self.name,
                "limit price must be positive",
                symbol=action.symbol,
                limit_price=str(action.limit_price),
            )
        last = ctx.quote(action.symbol)
        if last is None:
            return Verdict.veto(self.name, f"no quote for {action.symbol!r}", symbol=action.symbol)
        fill = ctx.project_fill(action)
        if fill is not None and last > ZERO:
            deviation = abs(fill.price - last) / last
            if deviation > self.price_sanity_pct:
                return Verdict.veto(
                    self.name,
                    f"fill price {fill.price} deviates {deviation:.4f} from last "
                    f"quote {last}, beyond the {self.price_sanity_pct} sanity band",
                    symbol=action.symbol,
                    fill_price=str(fill.price),
                    last_price=str(last),
                )
        return Verdict.passed(self.name)


@dataclass(frozen=True)
class CashConservation:
    """Ensure the projected fill conserves value (cash cannot be conjured).

    Formal property: applying the projected fill changes equity measured at the
    fill price by exactly ``-fees`` — i.e. ``cash_delta + price ×
    position_delta == -fees``, exactly, in Decimal. Reachable veto path: a
    tampered fill (see :func:`check_fill_conservation`), exercised by the F2
    benchmark and unit tests.
    """

    name: str = "cash_conservation"

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Pass unprojectable actions through; else verify fill economics."""
        fill = ctx.project_fill(action)
        if fill is None:
            return Verdict.passed(self.name)
        return check_fill_conservation(fill)


@dataclass(frozen=True)
class NoNegativeCash:
    """Forbid cash below ``-margin_limit`` (no implicit leverage by default).

    Formal property: ``state.cash + fill.cash_delta ≥ -margin_limit``. With the
    default ``margin_limit = 0`` the portfolio can never go cash-negative.
    """

    margin_limit: Decimal
    name: str = "no_negative_cash"

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Veto any action that would drive cash past the margin floor."""
        fill = ctx.project_fill(action)
        if fill is None:
            return Verdict.passed(self.name)
        post_cash = state.cash + fill.cash_delta
        if post_cash < -self.margin_limit:
            return Verdict.veto(
                self.name,
                f"post-fill cash {post_cash} would breach the margin floor {-self.margin_limit}",
                symbol=action.symbol,
                pre_cash=str(state.cash),
                post_cash=str(post_cash),
                margin_limit=str(self.margin_limit),
            )
        return Verdict.passed(self.name)


@dataclass(frozen=True)
class PositionLimit:
    """Cap per-symbol position and total gross exposure.

    Formal property: for the traded symbol ``|position_post| ≤
    max_position_per_symbol``, and ``Σ_i |position_i × price_i| ≤
    max_gross_exposure`` after the fill.
    """

    max_position_per_symbol: Decimal
    max_gross_exposure: Decimal
    name: str = "position_limit"

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Veto actions that push a position or gross exposure over its cap."""
        fill = ctx.project_fill(action)
        if fill is None:
            return Verdict.passed(self.name)
        post_position = state.position(action.symbol) + fill.position_delta
        if abs(post_position) > self.max_position_per_symbol:
            return Verdict.veto(
                self.name,
                f"position in {action.symbol} would reach {post_position}, over the "
                f"{self.max_position_per_symbol} cap",
                symbol=action.symbol,
                post_position=str(post_position),
                cap=str(self.max_position_per_symbol),
            )
        post_state = state.apply_fill(fill)
        gross = post_state.gross_exposure(ctx.last_price)
        if gross > self.max_gross_exposure:
            return Verdict.veto(
                self.name,
                f"gross exposure would reach {gross}, over the {self.max_gross_exposure} cap",
                symbol=action.symbol,
                gross_exposure=str(gross),
                cap=str(self.max_gross_exposure),
            )
        return Verdict.passed(self.name)


@dataclass(frozen=True)
class DrawdownBreaker:
    """Halt trading once equity falls too far from its high-water mark.

    Formal property (circuit breaker): if current equity ``≤ high_water_mark ×
    (1 - max_drawdown)`` then *every* action is vetoed, independent of the
    action itself. Evidence records the equity, the mark, and the threshold.
    """

    max_drawdown: Decimal
    name: str = "drawdown_breaker"

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Veto all trading while equity is below the drawdown threshold."""
        equity = state.equity(ctx.last_price)
        threshold = state.high_water_mark * (Decimal(1) - self.max_drawdown)
        if equity <= threshold:
            return Verdict.veto(
                self.name,
                f"equity {equity} is at/below the drawdown threshold {threshold} "
                f"({self.max_drawdown} off the {state.high_water_mark} high-water mark); "
                "trading halted",
                symbol=action.symbol,
                equity=str(equity),
                high_water_mark=str(state.high_water_mark),
                threshold=str(threshold),
            )
        return Verdict.passed(self.name)


@dataclass(frozen=True)
class RateLimit:
    """Cap order turnover per virtual-time window.

    Formal property: at most ``max_orders_per_window`` admitted orders may fall
    within any trailing ``rate_window``. An action is vetoed when the count of
    orders already recorded in the window has reached the cap.
    """

    max_orders_per_window: int
    rate_window: timedelta
    name: str = "rate_limit"

    def check(self, state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict:
        """Veto the order that would exceed the per-window turnover cap."""
        count = state.orders_within(self.rate_window, ctx.now)
        if count >= self.max_orders_per_window:
            return Verdict.veto(
                self.name,
                f"{count} orders already in the {self.rate_window} window, at the "
                f"{self.max_orders_per_window} cap",
                symbol=action.symbol,
                orders_in_window=count,
                cap=self.max_orders_per_window,
            )
        return Verdict.passed(self.name)


def default_invariants(risk: RiskConfig) -> list[Invariant]:
    """Build the standard v1 pipeline from a :class:`RiskConfig`.

    Order matters for evidence readability, not correctness: validity first,
    then conservation, then the economic and circuit-breaker checks.
    """
    return [
        OrderValidity(price_sanity_pct=risk.price_sanity_pct),
        CashConservation(),
        NoNegativeCash(margin_limit=risk.margin_limit),
        PositionLimit(
            max_position_per_symbol=risk.max_position_per_symbol,
            max_gross_exposure=risk.max_gross_exposure,
        ),
        DrawdownBreaker(max_drawdown=risk.max_drawdown),
        RateLimit(
            max_orders_per_window=risk.max_orders_per_window,
            rate_window=timedelta(seconds=risk.rate_window_seconds),
        ),
    ]
