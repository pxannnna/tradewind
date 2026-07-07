"""Scripted offline providers and a deterministic sample run, shared across tests."""

from datetime import timedelta
from pathlib import Path

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
from tradewind.trace.events import TraceHeader
from tradewind.trace.reader import read_trace
from tradewind.trace.writer import TraceWriter


class FrozenWallTimer:
    """WallTimer stand-in returning a constant latency, for reproducible recordings."""

    def start(self) -> float:
        return 0.0

    def elapsed_ms(self, start_marker: float) -> int:
        return 7


class ScriptedLLMProvider:
    """Offline 'LLM': response is a pure function of the request. Counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=f"scripted:{request.hash()[:12]}:{self.calls}",
            prompt_tokens=11,
            completion_tokens=5,
            cost_estimate="0.0001",
        )


class ScriptedDataProvider:
    """Offline market-data source: bars are a pure function of the request."""

    def __init__(self) -> None:
        self.calls = 0

    def read(self, request: DataRequest) -> DataResponse:
        self.calls += 1
        symbol = request.params.get("symbol", "?")
        return DataResponse(data={"symbol": symbol, "close": "101.25"})


def make_header(**overrides: object) -> TraceHeader:
    fields: dict[str, object] = {
        "config_hash": "c" * 64,
        "code_version": "0.1.0",
        "model_ids": ["scripted-model"],
        "submodule_sha": None,
        "rng_seed": 42,
    }
    fields.update(overrides)
    return TraceHeader.model_validate(fields)


def drive_sample_run(
    llm: LLMBoundary,
    data: DataBoundary,
    writer: TraceWriter,
    clock: VirtualClock,
    rand: SeededRand,
) -> None:
    """A deterministic mini agent loop exercising every boundary.

    Includes a repeated identical LLM request to exercise per-hash FIFO
    replay, and parent_seq links from decision back to its inputs.
    """
    bars, bars_event = data.read(DataRequest(source="bars", params={"symbol": "AAPL"}))
    prompt = LLMRequest(
        model="scripted-model",
        messages=[{"role": "user", "content": f"analyse {bars.data} at {clock.now().isoformat()}"}],
        temperature=0.0,
    )
    first, first_event = llm.complete(prompt, parent_seq=bars_event.seq)
    clock.advance(timedelta(minutes=1))
    # Identical request repeated: replay must return recordings in order.
    second, _ = llm.complete(prompt, parent_seq=bars_event.seq)
    writer.append(
        "agent_message",
        {"role": "trader", "content": first.content, "tiebreak": rand.randint(1, 100)},
        parent_seq=first_event.seq,
    )
    writer.append(
        "proposed_action",
        {"side": "buy", "symbol": "AAPL", "quantity": "10", "note": second.content},
        parent_seq=first_event.seq,
    )


def record_sample_trace(path: Path) -> tuple[ScriptedLLMProvider, ScriptedDataProvider]:
    """Record the sample run to ``path``; return the providers (for call counts)."""
    llm_provider = ScriptedLLMProvider()
    data_provider = ScriptedDataProvider()
    with TraceWriter(path, make_header()) as writer:
        llm = LLMBoundary(Mode.RECORD, writer, provider=llm_provider, wall_timer=FrozenWallTimer())
        data = DataBoundary(Mode.RECORD, writer, provider=data_provider)
        drive_sample_run(llm, data, writer, VirtualClock(), SeededRand(42))
    return llm_provider, data_provider


def replay_sample_trace(recorded: Path, out: Path) -> None:
    """Re-run the same driver against the recording; write the replayed trace to ``out``."""
    header, events = read_trace(recorded)
    index = ReplayIndex(events)
    with TraceWriter(out, header) as writer:
        llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
        data = DataBoundary(Mode.REPLAY, writer, replay_index=index)
        drive_sample_run(llm, data, writer, VirtualClock(), SeededRand(42))
