"""MCP server exposing the harness (Phase 6).

Tools — ``run_evaluation``, ``replay_trace``, ``verify_trace``, ``get_report``,
``list_invariants`` — are thin wrappers over the same library calls the CLI
uses. The tool handlers in :mod:`tradewind.mcp.server` are plain functions
returning JSON-native dicts, so they are testable without the ``mcp`` package;
:func:`~tradewind.mcp.server.create_server` (which needs the optional ``mcp``
dependency) merely registers them.
"""

from tradewind.mcp.server import (
    get_report,
    list_invariants,
    replay_trace,
    run_evaluation,
    verify_trace,
)

__all__ = [
    "get_report",
    "list_invariants",
    "replay_trace",
    "run_evaluation",
    "verify_trace",
]
