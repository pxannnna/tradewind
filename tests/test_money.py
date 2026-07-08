"""Money module: exact Decimal, float/bool rejection, one rounding rule."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tradewind.money import (
    bps_fraction,
    decimal_str,
    quantize_cents,
    quantize_qty,
    to_decimal,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.25", Decimal("1.25")), (5, Decimal(5)), (Decimal("3.30"), Decimal("3.30"))],
)
def test_to_decimal_accepts_exact_inputs(value: str | int | Decimal, expected: Decimal) -> None:
    assert to_decimal(value) == expected


@pytest.mark.parametrize("bad", [1.25, True, False])
def test_to_decimal_rejects_float_and_bool(bad: object) -> None:
    with pytest.raises(TypeError):
        to_decimal(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_to_decimal_rejects_non_finite(bad: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        to_decimal(bad)


def test_to_decimal_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not a valid decimal"):
        to_decimal("not-a-number")


def test_bps_fraction_is_exact() -> None:
    assert bps_fraction(10) == Decimal("0.001")
    assert bps_fraction(Decimal("2.5")) == Decimal("0.00025")


def test_quantize_helpers() -> None:
    assert quantize_cents(Decimal("1.239")) == Decimal("1.24")
    assert quantize_qty(Decimal("10.7")) == Decimal("10")
    assert quantize_qty(Decimal("10.7"), Decimal("5")) == Decimal("10")
    with pytest.raises(ValueError, match="positive"):
        quantize_qty(Decimal("1"), Decimal("0"))


def test_decimal_str_never_uses_scientific_notation() -> None:
    # A tiny fee that Decimal would otherwise render as 1E-8.
    assert decimal_str(Decimal("0.00000001")) == "0.00000001"
    assert "E" not in decimal_str(Decimal("100000000"))


@given(st.decimals(allow_nan=False, allow_infinity=False, places=8))
def test_decimal_str_roundtrips(value: Decimal) -> None:
    assert to_decimal(decimal_str(value)) == value
