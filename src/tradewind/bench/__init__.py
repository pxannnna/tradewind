"""Seeded-fault benchmark (Phase 4): proving the harness catches real failures.

`tradewind bench` runs a matrix of fault injections against the *real* harness
and reports, honestly, whether each was caught and by what mechanism. Every
scenario exercises the actual engine/portfolio/trace code and observes the
outcome — nothing is hard-coded to "detected".

Fault families:

* **F1 Hallucinated instruments** — orders for symbols outside the universe.
* **F2 Accounting attacks** — fills/actions that would create or destroy cash.
* **F3 Limit breaches** — position, gross-exposure, drawdown, and turnover.
* **F4 Nondeterminism injection** — a driver whose reissued requests differ on
  replay; the determinism check must flag it as a replay divergence.
* **F5 Trace tampering** — modified traces must fail verification.

The whole benchmark is deterministic and runs in CI with no API keys. See
:func:`tradewind.bench.report.run_benchmark`.
"""

from tradewind.bench.report import BenchReport, FaultResult, run_benchmark

__all__ = ["BenchReport", "FaultResult", "run_benchmark"]
