"""Trace capture and deterministic replay (Phase 1).

This package is the determinism foundation of Tradewind. It provides:

* :mod:`tradewind.trace.canonical` — canonical JSON and SHA-256 hashing.
* :mod:`tradewind.trace.events` — Pydantic-validated trace records.
* :mod:`tradewind.trace.writer` / :mod:`tradewind.trace.reader` — append-only
  JSONL trace files with a tamper-evident chain hash.
* :mod:`tradewind.trace.boundaries` — recordable interfaces (LLM, data,
  clock, RNG) that make every side effect replayable.

Nothing in this package may import an LLM SDK, an agent framework, the
wall clock, or the global :mod:`random` state (the sole exception is
:mod:`tradewind.trace.wallclock`, see its docstring).
"""

from tradewind.trace.boundaries import (
    DataBoundary,
    DataRequest,
    DataResponse,
    LLMBoundary,
    LLMRequest,
    LLMResponse,
    Mode,
    ReplayIndex,
    SeededRand,
    VirtualClock,
)
from tradewind.trace.canonical import canonical_json, request_hash, sha256_hex
from tradewind.trace.events import EventType, TraceEvent, TraceHeader
from tradewind.trace.reader import read_trace, verify_trace
from tradewind.trace.replay import replay_trace_file
from tradewind.trace.writer import TraceWriter

__all__ = [
    "DataBoundary",
    "DataRequest",
    "DataResponse",
    "EventType",
    "LLMBoundary",
    "LLMRequest",
    "LLMResponse",
    "Mode",
    "ReplayIndex",
    "SeededRand",
    "TraceEvent",
    "TraceHeader",
    "TraceWriter",
    "VirtualClock",
    "canonical_json",
    "read_trace",
    "replay_trace_file",
    "request_hash",
    "sha256_hex",
    "verify_trace",
]
