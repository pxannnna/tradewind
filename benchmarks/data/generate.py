"""Deterministically generate the bundled SYNTHETIC OHLCV snapshots.

Run:  python benchmarks/data/generate.py

Produces one CSV per symbol (columns: day,open,high,low,close,volume) of daily
bars over ~1 trading year. The data is a seeded geometric random walk — it is
NOT real market data (see PROVENANCE.md). The seed is fixed, so re-running this
script reproduces byte-identical files; the committed CSVs are the source of
truth for tests and the benchmark, and require no network or API key.
"""

import csv
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (symbol, starting price, seed, annual drift, daily vol) — arbitrary but fixed.
SYMBOLS = [
    ("AAPL", Decimal("180.00"), 11, Decimal("0.10"), Decimal("0.018")),
    ("MSFT", Decimal("330.00"), 22, Decimal("0.08"), Decimal("0.016")),
    ("SPY", Decimal("430.00"), 33, Decimal("0.06"), Decimal("0.010")),
]
TRADING_DAYS = 252
START = date(2023, 1, 3)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:  # Mon–Fri
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def generate_symbol(
    symbol: str, start_price: Decimal, seed: int, drift: Decimal, vol: Decimal
) -> None:
    rng = random.Random(seed)
    days = _weekdays(START, TRADING_DAYS)
    daily_drift = drift / Decimal(TRADING_DAYS)
    close = start_price
    path = HERE / f"{symbol}.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["day", "open", "high", "low", "close", "volume"])
        for day in days:
            prev_close = close
            shock = Decimal(str(round(rng.gauss(0.0, float(vol)), 6)))
            open_price = _cents(
                prev_close * (Decimal(1) + Decimal(str(round(rng.gauss(0, 0.003), 6))))
            )
            close = _cents(prev_close * (Decimal(1) + daily_drift + shock))
            hi = max(open_price, close) * (
                Decimal(1) + Decimal(str(abs(round(rng.gauss(0, 0.004), 6))))
            )
            lo = min(open_price, close) * (
                Decimal(1) - Decimal(str(abs(round(rng.gauss(0, 0.004), 6))))
            )
            volume = rng.randint(5_000_000, 60_000_000)
            writer.writerow([day.isoformat(), open_price, _cents(hi), _cents(lo), close, volume])
    print(f"wrote {path} ({TRADING_DAYS} bars)")


def main() -> None:
    for symbol, price, seed, drift, vol in SYMBOLS:
        generate_symbol(symbol, price, seed, drift, vol)


if __name__ == "__main__":
    main()
