# Bundled market data — provenance

**These files are SYNTHETIC. They are not real market data.**

`AAPL.csv`, `MSFT.csv`, and `SPY.csv` are a seeded geometric random walk
produced by [`generate.py`](generate.py) — the ticker names are labels for
liquid-instrument-shaped series, nothing more. No real prices, volumes, or
corporate actions are represented.

Why synthetic:

* **Determinism.** The generator uses a fixed seed, so the committed CSVs are
  byte-reproducible and the whole test/benchmark suite runs in CI with no
  network access and no API key.
* **Honesty.** Shipping clearly-labelled synthetic data is better than bundling
  real prices of uncertain licence or provenance and implying a fidelity the
  daily-bar simulator does not have. Overstating backtest fidelity is the
  classic sin this project's spec calls out; we avoid it by construction.

To regenerate identically:

```bash
python benchmarks/data/generate.py
```

## `golden/` — the golden-file fixture

`golden/AAPL.csv` is a tiny, hand-authored dataset with round-number prices so
the exact final portfolio state after a scripted run can be asserted by hand
(see `tests/test_sim.py::test_golden_scripted_run`). It, too, is synthetic.

## What the simulator does NOT model

The daily-bar replay is deliberately simple. It does not model intrabar price
paths, order books or depth, market impact, partial fills, bid/ask spreads
(beyond a flat slippage bps), borrow availability or short-sale constraints,
dividends, splits, or after-hours trading. Fills execute at the next bar's open
± a fixed slippage, with fees in basis points. See the README's Limitations
section.
