"""OHLCV bar data: load bundled snapshots, index by symbol and trading day.

Contract: :class:`PriceData` holds chronological daily bars for one or more
symbols over a shared, sorted set of trading days. Prices are exact Decimals
(loaded via :func:`tradewind.money.to_decimal`, so a malformed cell fails
loudly). All symbols must share the same day grid — a missing bar is a data
error, not something to silently interpolate.
"""

import csv
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from tradewind.money import to_decimal


@dataclass(frozen=True)
class Bar:
    """One daily OHLCV bar. OHLC are exact Decimals; volume is a whole count."""

    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class PriceData:
    """Chronological bars per symbol over a shared trading-day grid."""

    bars: Mapping[str, tuple[Bar, ...]]
    _index: dict[tuple[str, date], Bar] = field(default_factory=dict, repr=False, compare=False)
    _days: tuple[date, ...] = field(default_factory=tuple, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate alignment and build the ``(symbol, day) → Bar`` index."""
        if not self.bars:
            raise ValueError("PriceData needs at least one symbol")
        day_lists = {sym: [b.day for b in series] for sym, series in self.bars.items()}
        reference = next(iter(day_lists.values()))
        for sym, days in day_lists.items():
            if days != sorted(days):
                raise ValueError(f"bars for {sym} are not in chronological order")
            if days != reference:
                raise ValueError(f"symbol {sym} has a different trading-day grid than its peers")
        index = {(sym, b.day): b for sym, series in self.bars.items() for b in series}
        object.__setattr__(self, "_index", index)
        object.__setattr__(self, "_days", tuple(reference))

    @property
    def symbols(self) -> frozenset[str]:
        """The symbols present in this dataset."""
        return frozenset(self.bars)

    @property
    def trading_days(self) -> tuple[date, ...]:
        """The shared, chronologically-sorted trading days."""
        return self._days

    def bar_on(self, symbol: str, day: date) -> Bar | None:
        """Return the bar for ``symbol`` on ``day``, or ``None`` if absent."""
        return self._index.get((symbol, day))

    def opens_on(self, day: date, symbols: frozenset[str]) -> dict[str, Decimal]:
        """Open prices for ``symbols`` on ``day`` (skips any symbol without a bar)."""
        return {s: b.open for s in symbols if (b := self.bar_on(s, day)) is not None}

    def closes_on(self, day: date, symbols: frozenset[str]) -> dict[str, Decimal]:
        """Close prices for ``symbols`` on ``day`` (skips any symbol without a bar)."""
        return {s: b.close for s in symbols if (b := self.bar_on(s, day)) is not None}


def load_price_data(data_dir: Path | str, symbols: frozenset[str] | None = None) -> PriceData:
    """Load ``<symbol>.csv`` files (columns: day,open,high,low,close,volume).

    ``symbols`` restricts the load; ``None`` loads every ``*.csv`` in the
    directory. Prices parse through :func:`to_decimal`, so any non-numeric or
    non-finite cell raises rather than silently corrupting the series.
    """
    directory = Path(data_dir)
    if symbols is None:
        symbols = frozenset(p.stem for p in directory.glob("*.csv"))
    if not symbols:
        raise ValueError(f"no CSV data found in {directory}")
    bars: dict[str, tuple[Bar, ...]] = {}
    for symbol in sorted(symbols):
        path = directory / f"{symbol}.csv"
        if not path.exists():
            raise FileNotFoundError(f"no data file for {symbol}: {path}")
        rows: list[Bar] = []
        with path.open(newline="") as fh:
            for line_no, row in enumerate(csv.DictReader(fh), start=2):
                rows.append(
                    Bar(
                        day=date.fromisoformat(row["day"]),
                        open=to_decimal(row["open"]),
                        high=to_decimal(row["high"]),
                        low=to_decimal(row["low"]),
                        close=to_decimal(row["close"]),
                        volume=int(row["volume"]),
                    )
                )
                del line_no
        bars[symbol] = tuple(rows)
    return PriceData(bars=bars)
