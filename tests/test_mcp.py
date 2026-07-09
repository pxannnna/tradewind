"""MCP server: handler behaviour and FastMCP tool registration."""

from pathlib import Path

import pytest

from tradewind.errors import TraceIntegrityError
from tradewind.mcp import server

DATA = Path(__file__).resolve().parent.parent / "benchmarks" / "data"


def test_verify_and_replay_handlers(sample_trace: Path) -> None:
    verified = server.verify_trace(str(sample_trace))
    assert verified["verified"] is True
    assert verified["event_count"] == 5
    replayed = server.replay_trace(str(sample_trace))
    assert replayed["byte_identical"] is True
    assert replayed["final_chain_hash"] == verified["final_chain_hash"]


def test_verify_handler_propagates_integrity_errors(sample_trace: Path, tmp_path: Path) -> None:
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(sample_trace.read_bytes().replace(b'"AAPL"', b'"EVIL"', 1))
    with pytest.raises(TraceIntegrityError):
        server.verify_trace(str(tampered))


def test_list_invariants_names_all_six() -> None:
    invariants = server.list_invariants()
    names = [i["name"] for i in invariants]
    assert names == [
        "order_validity",
        "cash_conservation",
        "no_negative_cash",
        "position_limit",
        "drawdown_breaker",
        "rate_limit",
    ]
    assert all("Formal property" in i["property"] for i in invariants)


def test_run_evaluation_and_get_report(tmp_path: Path) -> None:
    trace_out = tmp_path / "run.jsonl"
    result = server.run_evaluation(str(DATA), symbol="AAPL", agent="sma", trace_out=str(trace_out))
    assert result["proposed"] == result["admitted"] == 15
    assert result["trace"] is not None and result["trace"]["verified"] is True

    report = server.get_report(str(trace_out), initial_cash="100000")
    assert report["attestation"]["replay_verified"] is True
    assert report["totals"]["admitted"] == 15


def test_run_evaluation_rejects_unknown_agent() -> None:
    with pytest.raises(ValueError, match="unknown agent"):
        server.run_evaluation(str(DATA), agent="yolo")


def test_create_server_registers_all_tools() -> None:
    pytest.importorskip("mcp")
    import anyio

    mcp_server = server.create_server()
    tools = anyio.run(mcp_server.list_tools)
    assert {t.name for t in tools} == {
        "verify_trace",
        "replay_trace",
        "get_report",
        "list_invariants",
        "run_evaluation",
    }
