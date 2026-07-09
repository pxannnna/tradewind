"""Adapter pieces that depend on the optional heavy extras.

Runs whichever side is possible in the current environment: with the extras
absent (the default dev/CI environment) it proves the loud, specific
dependency errors; with them installed it exercises the boundary chat model
against a record-mode boundary.
"""

from pathlib import Path

import pytest

from tests.helpers import ScriptedLLMProvider, make_header
from tradewind.adapters.tradingagents.boundary_model import (
    AdapterDependencyMissing,
    BoundaryLLMClient,
    make_boundary_chat_model,
)
from tradewind.trace.boundaries import LLMBoundary, Mode
from tradewind.trace.reader import read_trace
from tradewind.trace.writer import TraceWriter


def _has(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def test_boundary_model_without_langchain_raises_clear_error(tmp_path: Path) -> None:
    if _has("langchain_core"):
        pytest.skip("langchain_core installed; the missing-dep path is untestable here")
    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        boundary = LLMBoundary(Mode.RECORD, writer, provider=ScriptedLLMProvider())
        with pytest.raises(AdapterDependencyMissing, match="tradewind\\[tradingagents\\]"):
            make_boundary_chat_model(boundary, "m")


def test_boundary_model_generates_through_boundary(tmp_path: Path) -> None:
    if not _has("langchain_core"):
        pytest.skip("langchain_core not installed (tradingagents extra)")
    from langchain_core.messages import HumanMessage

    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        boundary = LLMBoundary(Mode.RECORD, writer, provider=ScriptedLLMProvider())
        model = BoundaryLLMClient(boundary, "scripted-model", role="trader").get_llm()
        result = model.invoke([HumanMessage(content="assess AAPL")])
    assert result.content.startswith("scripted:")
    _, events = read_trace(tmp_path / "t.jsonl")
    llm_events = [e for e in events if e.event_type == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0].payload["role"] == "trader"
    assert llm_events[0].payload["request"]["messages"][0]["role"] == "user"


def test_adapter_without_tradingagents_raises_clear_error(tmp_path: Path) -> None:
    if _has("tradingagents"):
        pytest.skip("tradingagents installed; the missing-dep path is untestable here")
    from tradewind.adapters.tradingagents.adapter import patched_llm_clients

    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        boundary = LLMBoundary(Mode.RECORD, writer, provider=ScriptedLLMProvider())
        with (
            pytest.raises(AdapterDependencyMissing, match="third_party/TradingAgents"),
            patched_llm_clients(boundary),
        ):
            pass  # pragma: no cover - never reached
