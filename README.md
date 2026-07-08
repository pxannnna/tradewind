# Tradewind

Deterministic verification and evaluation harness for LLM trading agents.

**Agents propose, the harness disposes.** Tradewind wraps LLM-based
trading-agent frameworks to make their runs bit-for-bit reproducible
(deterministic replay of every LLM call and market-data read), enforce hard
risk invariants *outside* the model, and produce diffable, auditable
evaluation reports.

> This README is a placeholder written during Phase 1. The full writeup —
> quickstart, architecture diagram, invariant list, benchmark results, and
> Limitations — is authored last (see `HARNESS_BUILD_SPEC.md` §10). Design
> decisions are logged in [`DECISIONS.md`](DECISIONS.md).

## Status

- **Phase 1 — trace capture & deterministic replay:** implemented.
- **Phase 2 — invariant engine:** implemented (six pure risk invariants +
  conservation-enforcing portfolio, rogue-agent containment proven).
- **Phase 3 — market replay simulator & paper fills:** implemented (bar-replay
  over bundled synthetic OHLCV, next-open fills with slippage/fees vetted by
  the invariant engine, golden-file test, `tradewind run`).
- **Phase 4 — seeded-fault benchmark:** implemented — 17 scenarios across F1–F5,
  **17/17 caught**; results in [`benchmarks/results/`](benchmarks/results/).
- Phases 5–6: not started.
