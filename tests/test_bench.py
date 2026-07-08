"""Benchmark: all critical faults caught, determinism, and genuine-nondeterminism F4.

The `test_bench.py` module is allowed to use real ``time``/``random`` (the
no-wallclock lint only guards ``src/tradewind``), so here we additionally prove
the F4 catch against genuine nondeterminism, not just the counter stand-in.
"""

import random
import time
from pathlib import Path

from tests.helpers import make_header
from tradewind.bench import run_benchmark
from tradewind.bench.report import CRITICAL_FAMILIES
from tradewind.errors import ReplayDivergence
from tradewind.trace.boundaries import (
    LLMBoundary,
    LLMRequest,
    LLMResponse,
    Mode,
    ReplayIndex,
)
from tradewind.trace.reader import read_trace
from tradewind.trace.writer import TraceWriter


def test_all_scenarios_detected() -> None:
    report = run_benchmark()
    for result in report.results:
        assert result.detected, f"{result.fault_id} {result.scenario} NOT detected"


def test_at_least_fifteen_scenarios_across_five_families() -> None:
    report = run_benchmark()
    assert len(report.results) >= 15
    assert set(report.families()) == {"F1", "F2", "F3", "F4", "F5"}
    for fid in report.families():
        assert report.catch_rate(fid)[1] >= 3, f"{fid} has fewer than 3 scenarios"


def test_critical_families_all_caught() -> None:
    report = run_benchmark()
    assert report.critical_all_caught
    for fid in CRITICAL_FAMILIES:
        caught, total = report.catch_rate(fid)
        assert caught == total > 0


def test_benchmark_is_deterministic() -> None:
    """The whole benchmark reproduces byte-identically (committed results depend on it)."""
    assert run_benchmark().to_markdown() == run_benchmark().to_markdown()
    assert run_benchmark().to_json() == run_benchmark().to_json()


def test_report_renders_json_and_markdown() -> None:
    report = run_benchmark()
    payload = report.to_json()
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert summary["caught"] == summary["total"]
    md = report.to_markdown()
    assert "seeded-fault benchmark" in md
    assert "F4 caveat" in md


class _ConstLLM:
    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="ok")


def _record_then_replay_with_noise(tmp_path: Path, noise: object) -> bool:
    """Record an LLM call whose prompt embeds ``noise()``; replay it; caught?"""
    header = make_header()

    def request() -> LLMRequest:
        return LLMRequest(model="m", messages=[{"role": "user", "content": f"n={noise()}"}])  # type: ignore[operator]

    recorded = tmp_path / "rec.jsonl"
    with TraceWriter(recorded, header) as writer:
        LLMBoundary(Mode.RECORD, writer, provider=_ConstLLM()).complete(request())
    _, events = read_trace(recorded)
    index = ReplayIndex(events)
    with TraceWriter(tmp_path / "rep.jsonl", header) as writer:
        replay_llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
        try:
            replay_llm.complete(request())
        except ReplayDivergence:
            return True
    return False


def test_f4_catches_genuine_unseeded_rng(tmp_path: Path) -> None:
    rng = random.Random()  # deliberately unseeded → nondeterministic across calls
    assert _record_then_replay_with_noise(tmp_path, lambda: rng.random())


def test_f4_catches_genuine_wall_clock(tmp_path: Path) -> None:
    assert _record_then_replay_with_noise(tmp_path, time.perf_counter_ns)


def test_bench_cli_writes_results(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from tradewind.cli import app

    result = CliRunner().invoke(app, ["bench", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "ALL: 17/17 caught" in result.output
    assert (tmp_path / "results.md").exists()
    assert (tmp_path / "results.json").exists()


def test_bench_cli_without_out_prints_only() -> None:
    from typer.testing import CliRunner

    from tradewind.cli import app

    result = CliRunner().invoke(app, ["bench"])
    assert result.exit_code == 0
    assert "F1:" in result.output and "ALL:" in result.output
