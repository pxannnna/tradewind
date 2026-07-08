"""PriceData loading and alignment validation."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tradewind.sim.bars import Bar, PriceData, load_price_data

DATA = Path(__file__).resolve().parent.parent / "benchmarks" / "data"


def test_load_synthetic_dataset() -> None:
    prices = load_price_data(DATA, frozenset({"AAPL", "MSFT"}))
    assert prices.symbols == frozenset({"AAPL", "MSFT"})
    assert len(prices.trading_days) == 252
    first = prices.trading_days[0]
    bar = prices.bar_on("AAPL", first)
    assert bar is not None and bar.open > 0


def test_load_missing_symbol_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no data file"):
        load_price_data(tmp_path, frozenset({"NOPE"}))


def test_load_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no CSV data"):
        load_price_data(tmp_path)


def test_opens_and_closes_on() -> None:
    prices = load_price_data(DATA / "golden", frozenset({"AAPL"}))
    day = date(2024, 1, 2)
    assert prices.opens_on(day, frozenset({"AAPL"})) == {"AAPL": Decimal("100.00")}
    assert prices.closes_on(day, frozenset({"AAPL"})) == {"AAPL": Decimal("100.00")}


def test_misaligned_grid_rejected() -> None:
    a = (Bar(date(2024, 1, 2), *[Decimal(1)] * 4, 1),)
    b = (Bar(date(2024, 1, 3), *[Decimal(1)] * 4, 1),)
    with pytest.raises(ValueError, match="different trading-day grid"):
        PriceData(bars={"A": a, "B": b})


def test_out_of_order_rejected() -> None:
    unsorted = (
        Bar(date(2024, 1, 3), *[Decimal(1)] * 4, 1),
        Bar(date(2024, 1, 2), *[Decimal(1)] * 4, 1),
    )
    with pytest.raises(ValueError, match="chronological"):
        PriceData(bars={"A": unsorted})


def test_empty_pricedata_rejected() -> None:
    with pytest.raises(ValueError, match="at least one symbol"):
        PriceData(bars={})
