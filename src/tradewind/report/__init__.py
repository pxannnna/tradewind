"""Evaluation reports (Phase 5): render an audited, diffable view of a run.

`tradewind report` turns a verified trace into a self-contained HTML page plus
machine-readable JSON: the run config, a P&L/equity curve (dependency-free
SVG), a per-decision table that links each fill back through ``parent_seq`` to
the agent messages and LLM calls that produced it, every violation with its
evidence, token/cost totals per agent role, and a determinism attestation
(chain hash + replay-verified flag).

`tradewind diff` aligns two traces and reports where their decisions first
diverged.

Nothing here mutates a trace or performs network I/O; building a report first
fully verifies the trace's integrity.
"""

from tradewind.report.diff import DiffResult, diff_traces
from tradewind.report.model import ReportModel, build_report_model
from tradewind.report.render import render_diff_html, render_report_html

__all__ = [
    "DiffResult",
    "ReportModel",
    "build_report_model",
    "diff_traces",
    "render_diff_html",
    "render_report_html",
]
