"""Acceptance: replay is byte-identical with zero live calls; misses are hard errors."""

from pathlib import Path

import pytest

from tests.helpers import (
    FrozenWallTimer,
    ScriptedLLMProvider,
    drive_sample_run,
    make_header,
    record_sample_trace,
    replay_sample_trace,
)
from tradewind.errors import ReplayDivergence
from tradewind.trace.boundaries import (
    DataBoundary,
    LLMBoundary,
    LLMRequest,
    Mode,
    ReplayIndex,
    SeededRand,
    VirtualClock,
)
from tradewind.trace.reader import read_trace, verify_trace
from tradewind.trace.replay import replay_trace_file
from tradewind.trace.writer import TraceWriter


def test_driver_replay_is_byte_identical(tmp_path: Path) -> None:
    """Re-running the same program against the recording reproduces the trace exactly."""
    recorded = tmp_path / "recorded.jsonl"
    llm_provider, data_provider = record_sample_trace(recorded)
    assert llm_provider.calls == 2
    assert data_provider.calls == 1

    replayed = tmp_path / "replayed.jsonl"
    replay_sample_trace(recorded, replayed)

    assert recorded.read_bytes() == replayed.read_bytes()
    assert verify_trace(recorded).final_chain_hash == verify_trace(replayed).final_chain_hash
    # Providers were never touched again: replay boundaries cannot hold one.
    assert llm_provider.calls == 2
    assert data_provider.calls == 1


def test_trace_level_replay_is_byte_identical(sample_trace: Path, tmp_path: Path) -> None:
    """`tradewind replay`'s engine: re-emit the stream, assert equal chain hash."""
    out = tmp_path / "replay-out.jsonl"
    report = replay_trace_file(sample_trace, out)
    assert report.byte_identical
    assert report.event_count == 5
    assert report.final_chain_hash == verify_trace(sample_trace).final_chain_hash
    assert out.read_bytes() == sample_trace.read_bytes()


def test_replay_mode_cannot_hold_a_live_provider(sample_trace: Path, tmp_path: Path) -> None:
    """Structural no-network guarantee: attaching a provider in replay mode is an error."""
    from tradewind.trace.boundaries import BoundaryConfigError

    _, events = read_trace(sample_trace)
    index = ReplayIndex(events)
    with (
        TraceWriter(tmp_path / "t.jsonl", make_header()) as writer,
        pytest.raises(BoundaryConfigError, match="must not have a live provider"),
    ):
        LLMBoundary(Mode.REPLAY, writer, provider=ScriptedLLMProvider(), replay_index=index)


def test_unrecorded_request_raises_replay_divergence(sample_trace: Path, tmp_path: Path) -> None:
    _, events = read_trace(sample_trace)
    index = ReplayIndex(events)
    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
        novel = LLMRequest(model="scripted-model", messages=[{"role": "user", "content": "??"}])
        with pytest.raises(ReplayDivergence, match="no recorded llm_call"):
            llm.complete(novel)


def test_exhausted_recording_raises_replay_divergence(sample_trace: Path, tmp_path: Path) -> None:
    """Calling one more time than the recording did is a divergence, not a fallback."""
    header, events = read_trace(sample_trace)
    index = ReplayIndex(events)
    repeated = LLMRequest.model_validate(
        next(e for e in events if e.event_type == "llm_call").payload["request"]
    )
    with TraceWriter(tmp_path / "t.jsonl", header) as writer:
        llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
        llm.complete(repeated)
        llm.complete(repeated)  # both recordings consumed
        with pytest.raises(ReplayDivergence, match="exhausted"):
            llm.complete(repeated)


def test_repeated_identical_requests_replay_in_recorded_order(
    sample_trace: Path, tmp_path: Path
) -> None:
    header, events = read_trace(sample_trace)
    llm_events = [e for e in events if e.event_type == "llm_call"]
    assert len(llm_events) == 2
    assert llm_events[0].payload["request_hash"] == llm_events[1].payload["request_hash"]
    index = ReplayIndex(events)
    repeated = LLMRequest.model_validate(llm_events[0].payload["request"])
    with TraceWriter(tmp_path / "t.jsonl", header) as writer:
        llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
        first, _ = llm.complete(repeated)
        second, _ = llm.complete(repeated)
    assert first.content.endswith(":1")
    assert second.content.endswith(":2")
    assert first.content != second.content


def test_divergent_driver_produces_different_chain(tmp_path: Path) -> None:
    """A driver that behaves differently cannot silently produce the same trace."""
    recorded = tmp_path / "recorded.jsonl"
    record_sample_trace(recorded)
    header, events = read_trace(recorded)
    index = ReplayIndex(events)
    out = tmp_path / "divergent.jsonl"
    with TraceWriter(out, header) as writer:
        llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
        data = DataBoundary(Mode.REPLAY, writer, replay_index=index)
        # Same driver but a different virtual start time changes the prompt,
        # which must surface as a ReplayDivergence, never a live call.
        from datetime import UTC, datetime

        clock = VirtualClock(start=datetime(2001, 1, 1, tzinfo=UTC))
        with pytest.raises(ReplayDivergence):
            drive_sample_run(llm, data, writer, clock, SeededRand(42))


def test_recording_never_overwrites(tmp_path: Path) -> None:
    from tradewind.trace.writer import TraceWriteError

    target = tmp_path / "trace.jsonl"
    record_sample_trace(target)
    with pytest.raises(TraceWriteError, match="already exists"):
        TraceWriter(target, make_header())


def test_frozen_timer_keeps_recordings_reproducible(tmp_path: Path) -> None:
    """Two recordings of the same deterministic run are themselves byte-identical."""
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    record_sample_trace(a)
    record_sample_trace(b)
    assert a.read_bytes() == b.read_bytes()
    assert isinstance(FrozenWallTimer().elapsed_ms(0.0), int)
