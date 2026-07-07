"""Clock/RNG determinism and boundary construction rules."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.helpers import ScriptedDataProvider, ScriptedLLMProvider, make_header
from tradewind.trace.boundaries import (
    BoundaryConfigError,
    DataBoundary,
    LLMBoundary,
    LLMRequest,
    Mode,
    ReplayIndex,
    SeededRand,
    VirtualClock,
)
from tradewind.trace.writer import TraceWriter


def test_virtual_clock_only_moves_when_advanced() -> None:
    clock = VirtualClock(start=datetime(2024, 1, 2, tzinfo=UTC))
    assert clock.now() == clock.now()
    clock.advance(timedelta(days=1))
    assert clock.now() == datetime(2024, 1, 3, tzinfo=UTC)
    clock.set_time(datetime(2024, 2, 1, tzinfo=UTC))
    assert clock.now() == datetime(2024, 2, 1, tzinfo=UTC)


def test_virtual_clock_rejects_time_travel_and_naive_datetimes() -> None:
    clock = VirtualClock(start=datetime(2024, 1, 2, tzinfo=UTC))
    with pytest.raises(BoundaryConfigError):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(BoundaryConfigError):
        clock.set_time(datetime(2023, 1, 1, tzinfo=UTC))
    with pytest.raises(BoundaryConfigError):
        clock.set_time(datetime(2025, 1, 1))  # naive
    with pytest.raises(BoundaryConfigError):
        VirtualClock(start=datetime(2024, 1, 1))  # naive


def test_seeded_rand_is_deterministic_and_isolated() -> None:
    a, b = SeededRand(7), SeededRand(7)
    seq_a = [a.random(), float(a.randint(0, 10**6)), a.uniform(-1, 1), float(a.choice([1, 2, 3]))]
    seq_b = [b.random(), float(b.randint(0, 10**6)), b.uniform(-1, 1), float(b.choice([1, 2, 3]))]
    assert seq_a == seq_b
    assert SeededRand(8).random() != SeededRand(7).random()


def test_record_mode_requires_provider(tmp_path: Path) -> None:
    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        with pytest.raises(BoundaryConfigError, match="requires a live provider"):
            LLMBoundary(Mode.RECORD, writer)
        with pytest.raises(BoundaryConfigError, match="requires a live provider"):
            DataBoundary(Mode.RECORD, writer)


def test_replay_mode_requires_index(tmp_path: Path) -> None:
    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        with pytest.raises(BoundaryConfigError, match="requires a replay index"):
            LLMBoundary(Mode.REPLAY, writer)
        with pytest.raises(BoundaryConfigError, match="requires a replay index"):
            DataBoundary(Mode.REPLAY, writer)
        with pytest.raises(BoundaryConfigError, match="must not have a live provider"):
            DataBoundary(Mode.REPLAY, writer, provider=ScriptedDataProvider())


def test_request_hash_covers_all_fields() -> None:
    base = LLMRequest(model="m", messages=[{"role": "user", "content": "x"}])
    assert base.hash() == base.model_copy().hash()
    assert base.hash() != base.model_copy(update={"temperature": 0.5}).hash()
    assert base.hash() != base.model_copy(update={"model": "m2"}).hash()
    assert base.hash() != base.model_copy(update={"params": {"top_p": 1}}).hash()


def test_llm_event_payload_shape(tmp_path: Path) -> None:
    provider = ScriptedLLMProvider()
    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        llm = LLMBoundary(Mode.RECORD, writer, provider=provider)
        request = LLMRequest(model="m", messages=[{"role": "user", "content": "x"}])
        response, event = llm.complete(request)
    assert event.event_type == "llm_call"
    assert event.payload["request_hash"] == request.hash()
    assert event.payload["request"] == request.model_dump(mode="json")
    assert event.payload["response"]["content"] == response.content
    assert event.payload["token_counts"] == {"prompt": 11, "completion": 5}
    assert event.payload["cost_estimate"] == "0.0001"
    assert isinstance(event.payload["latency_ms"], int)


def test_replay_index_pending_counts(sample_trace: Path) -> None:
    from tradewind.trace.reader import read_trace

    _, events = read_trace(sample_trace)
    index = ReplayIndex(events)
    assert index.pending() == 3  # two llm calls + one data read
