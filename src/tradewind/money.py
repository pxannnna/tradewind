"""Monetary and quantity arithmetic — exact ``Decimal``, one rounding rule.

Contract (the single place rounding is defined; every other module defers here):

* Every monetary amount, price, quantity, and rate in Tradewind is an exact
  :class:`decimal.Decimal`.
* **The accounting path performs no rounding.** Notionals (``price × qty``),
  fees (``notional × rate``), and cash deltas are exact products and sums, so
  the cash-conservation identity holds with a zero residual — not a residual
  smaller than some epsilon, exactly zero. This is why money is never a
  binary ``float``: ``0.1 + 0.2 != 0.3`` in IEEE-754, which would silently
  break conservation. :func:`to_decimal` therefore *rejects* ``float`` (and
  ``bool``) inputs outright; callers must pass ``Decimal``, ``int``, or a
  decimal string.
* Rates are expressed in basis points (1 bp = 1/10 000); :func:`bps_fraction`
  converts them to exact Decimal fractions.
* Rounding to a display or exchange grid, if ever required, happens only
  through the explicit :func:`quantize_cents` / :func:`quantize_qty` helpers
  at I/O boundaries — never inside invariant or portfolio arithmetic.
"""

from decimal import Decimal, InvalidOperation

#: Basis-points denominator (1 bp = 1/10 000).
BPS_DENOMINATOR = Decimal(10000)

#: Canonical zero, to avoid re-constructing it in hot paths.
ZERO = Decimal(0)


def to_decimal(value: str | int | Decimal) -> Decimal:
    """Coerce ``value`` to an exact, finite ``Decimal``; reject float/bool/NaN.

    ``bool`` is rejected even though it is an ``int`` subclass: ``True`` as a
    monetary amount is always a bug. ``float`` is rejected because it cannot
    represent decimal cents exactly.
    """
    if isinstance(value, bool):
        raise TypeError("money/quantity must not be a bool")
    if isinstance(value, float):
        raise TypeError(
            "money/quantity must not be a float (binary floats break exact "
            "conservation); pass a Decimal, int, or decimal string"
        )
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a valid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"money/quantity must be finite, got {result}")
    return result


def bps_fraction(bps: str | int | Decimal) -> Decimal:
    """Convert a basis-points rate to an exact Decimal fraction (10 bp → 0.001)."""
    return to_decimal(bps) / BPS_DENOMINATOR


def quantize_cents(amount: Decimal) -> Decimal:
    """Round an amount to whole cents (I/O boundary only, never in accounting)."""
    return amount.quantize(Decimal("0.01"))


def quantize_qty(quantity: Decimal, step: Decimal = Decimal(1)) -> Decimal:
    """Round a quantity down to a whole multiple of ``step`` (I/O boundary only)."""
    if step <= ZERO:
        raise ValueError("lot step must be positive")
    return (quantity // step) * step


def decimal_str(amount: Decimal) -> str:
    """Serialise a Decimal for a JSON-native trace payload (money is never float)."""
    return format(amount, "f")
