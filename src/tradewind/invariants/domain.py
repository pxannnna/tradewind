"""Domain value objects for the invariant engine.

Contract: these are frozen, exact-Decimal value objects with no I/O and no
dependency on any LLM library. ``MarketContext.project_fill`` is the single
deterministic map from an agent's proposed order to the paper fill the harness
would execute; it is *total* — an unprojectable action (unknown symbol, no
quote, non-positive quantity) yields ``None`` rather than raising, so
:class:`~tradewind.invariants.checks.OrderValidity` owns that veto and the
economic invariants stay quiet.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from tradewind.money import ZERO, bps_fraction, decimal_str, to_decimal


class Side(Enum):
    """Order side. ``sign`` is +1 for buys, -1 for sells."""

    BUY = "buy"
    SELL = "sell"

    def sign(self) -> Decimal:
        """Return +1 for a buy, -1 for a sell."""
        return Decimal(1) if self is Side.BUY else Decimal(-1)


@dataclass(frozen=True)
class ProposedAction:
    """An order an agent proposes. Untrusted: fields may be malformed.

    ``quantity`` and ``limit_price`` are exact Decimals; the harness never
    assumes they are positive or finite — that is exactly what
    :class:`~tradewind.invariants.checks.OrderValidity` verifies.
    """

    symbol: str
    side: Side
    quantity: Decimal
    limit_price: Decimal | None = None

    def to_payload(self) -> dict[str, str | None]:
        """JSON-native form for a ``proposed_action`` trace event."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": decimal_str(self.quantity),
            "limit_price": None if self.limit_price is None else decimal_str(self.limit_price),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, str | None]) -> "ProposedAction":
        """Reconstruct from a ``proposed_action`` trace payload."""
        raw_side = payload["side"]
        raw_qty = payload["quantity"]
        if raw_side is None or raw_qty is None:
            raise ValueError("proposed_action payload missing side/quantity")
        limit = payload.get("limit_price")
        return cls(
            symbol=str(payload["symbol"]),
            side=Side(raw_side),
            quantity=to_decimal(raw_qty),
            limit_price=None if limit is None else to_decimal(limit),
        )


@dataclass(frozen=True)
class Fill:
    """A realised paper fill with its exact economic effect.

    ``cash_delta`` and ``position_delta`` are stored explicitly so that a
    *tampered* fill (one whose claimed cash effect does not match its own
    side/price/quantity/fees) can be represented and then caught by the cash-
    conservation check. Use :meth:`honest` to build a consistent fill.
    """

    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fees: Decimal
    cash_delta: Decimal
    position_delta: Decimal

    @classmethod
    def honest(
        cls, symbol: str, side: Side, quantity: Decimal, price: Decimal, fees: Decimal
    ) -> "Fill":
        """Build the internally-consistent fill for these economics.

        ``position_delta = sign · qty`` and
        ``cash_delta = -sign · price · qty - fees`` (a buy spends cash and
        receives shares; a sell receives cash net of fees).
        """
        sign = side.sign()
        return cls(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            fees=fees,
            cash_delta=-sign * price * quantity - fees,
            position_delta=sign * quantity,
        )

    def to_payload(self) -> dict[str, str]:
        """JSON-native form for a ``fill`` trace event."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": decimal_str(self.quantity),
            "price": decimal_str(self.price),
            "fees": decimal_str(self.fees),
            "cash_delta": decimal_str(self.cash_delta),
            "position_delta": decimal_str(self.position_delta),
        }


@dataclass(frozen=True)
class MarketModel:
    """Deterministic microstructure: fees and slippage, both in basis points.

    A buy fills at ``last × (1 + slippage)``, a sell at ``last × (1 - slippage)``;
    fees are ``notional × fee_bps``. This is intentionally simple — see the
    README's Limitations section for what a bar-level model does not capture.
    """

    fee_bps: Decimal = ZERO
    slippage_bps: Decimal = ZERO


@dataclass(frozen=True)
class MarketContext:
    """Market facts at one decision point: time, quotes, tradeable universe."""

    now: datetime
    last_price: Mapping[str, Decimal]
    tradeable_universe: frozenset[str]
    model: MarketModel = field(default_factory=MarketModel)

    def quote(self, symbol: str) -> Decimal | None:
        """Return the last price for ``symbol``, or ``None`` if unquoted."""
        return self.last_price.get(symbol)

    def project_fill(self, action: ProposedAction) -> Fill | None:
        """Map a proposed order to its honest paper fill, or ``None`` if unprojectable.

        Total function: returns ``None`` when the symbol is outside the
        universe, has no quote, or the quantity is non-finite or non-positive.
        """
        if action.symbol not in self.tradeable_universe:
            return None
        last = self.quote(action.symbol)
        if last is None:
            return None
        if not action.quantity.is_finite() or action.quantity <= ZERO:
            return None
        slip = bps_fraction(self.model.slippage_bps)
        fill_price = last * (Decimal(1) + action.side.sign() * slip)
        notional = fill_price * action.quantity
        fees = notional * bps_fraction(self.model.fee_bps)
        return Fill.honest(action.symbol, action.side, action.quantity, fill_price, fees)


@dataclass(frozen=True)
class RiskConfig:
    """Risk thresholds that parameterise the invariant pipeline.

    Separate from :class:`MarketModel` (microstructure) and
    :class:`MarketContext` (facts): these are the operator's policy knobs.
    Fractions (``max_drawdown``, ``price_sanity_pct``) are expressed as
    fractions of 1, e.g. ``Decimal("0.20")`` for 20%.
    """

    max_position_per_symbol: Decimal
    max_gross_exposure: Decimal
    max_drawdown: Decimal
    price_sanity_pct: Decimal
    max_orders_per_window: int
    rate_window_seconds: int
    margin_limit: Decimal = ZERO
