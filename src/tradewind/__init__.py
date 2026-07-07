"""Tradewind: deterministic verification and evaluation harness for LLM trading agents.

Core thesis: agents propose, the harness disposes. The trace layer makes every
run bit-for-bit replayable; the invariant layer vetoes unsafe actions outside
the model; reports make every decision auditable.

The core packages (``trace``, ``invariants``, ``sim``, ``report``) are
framework-agnostic and must never import any agent framework or LLM SDK.
Only ``tradewind.adapters.*`` may do that.
"""

from tradewind.errors import ReplayDivergence, TraceIntegrityError, TradewindError

__all__ = ["ReplayDivergence", "TraceIntegrityError", "TradewindError"]

__version__ = "0.1.0"
