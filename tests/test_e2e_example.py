"""Acceptance (Phase 6): the committed example trace replays with no API key.

This is the clean-clone guarantee: `examples/traces/tradingagents_demo.jsonl`
is committed, and this test — which runs in CI with no secrets — verifies it,
replays it byte-identically, and builds the evaluation report from it.
"""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from tradewind.report.model import build_report_model
from tradewind.trace.reader import verify_trace
from tradewind.trace.replay import replay_trace_file

REPO = Path(__file__).resolve().parent.parent
TRACE = REPO / "examples" / "traces" / "tradingagents_demo.jsonl"


def test_committed_trace_exists_and_verifies() -> None:
    assert TRACE.exists(), "the committed example trace must ship with the repo"
    result = verify_trace(TRACE)
    assert result.event_count > 5
    assert len(result.final_chain_hash) == 64


def test_committed_trace_replays_byte_identically() -> None:
    report = replay_trace_file(TRACE)
    assert report.byte_identical


def test_committed_trace_report_shows_full_deliberation() -> None:
    model = build_report_model(TRACE, initial_equity=Decimal("10000"))
    assert model.replay_verified is True
    assert model.proposed == 1 and model.admitted == 1
    # The decision's provenance walks back through the deliberation chain.
    provenance_roles = [p.get("role") for p in model.decisions[0].provenance]
    assert provenance_roles[-1] == "risk_judge"
    assert "market_analyst" in provenance_roles
    # Role-tagged LLM usage is totalled per agent role.
    roles = {u.role for u in model.usage}
    assert {"market_analyst", "news_analyst", "trader", "risk_judge"} <= roles


def test_example_script_runs_end_to_end() -> None:
    """The runnable example itself succeeds against the committed trace."""
    result = subprocess.run(
        [sys.executable, str(REPO / "examples" / "tradingagents_e2e.py")],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "byte-identical = True" in result.stdout
    assert "replay_verified=True" in result.stdout
