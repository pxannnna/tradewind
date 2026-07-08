"""Market replay simulator and paper fills (Phase 3).

A deterministic bar-replay engine over bundled OHLCV snapshots. Agents propose
orders at a bar's close using only data up to that bar; the harness executes
admitted orders at the **next bar's open** (with configurable slippage and
fees), running the full invariant pipeline against the *actual* execution
economics so no invariant can be violated in the resulting portfolio.

This is a deliberately simple, honest model. See the README's Limitations
section for what a daily-bar simulator does **not** capture (intrabar price
paths, order books, market impact, partial fills, borrow/short constraints).

Public surface:

* :class:`Bar`, :class:`PriceData` — OHLCV data and loading.
* :class:`Simulator`, :class:`SimConfig`, :class:`SimulationResult`,
  :class:`EquityPoint`, :class:`DecisionContext`, :class:`Agent`.
* :mod:`tradewind.sim.agents` — reusable scripted agents.
"""

from tradewind.sim.bars import Bar, PriceData, load_price_data
from tradewind.sim.simulator import (
    Agent,
    DecisionContext,
    EquityPoint,
    SimConfig,
    SimulationResult,
    Simulator,
)

__all__ = [
    "Agent",
    "Bar",
    "DecisionContext",
    "EquityPoint",
    "PriceData",
    "SimConfig",
    "SimulationResult",
    "Simulator",
    "load_price_data",
]
