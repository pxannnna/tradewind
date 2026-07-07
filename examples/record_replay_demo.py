"""Record a run with an offline scripted 'LLM', then replay it byte-identically.

Run:  python examples/record_replay_demo.py
Needs no network and no API keys. Demonstrates the Phase 1 loop:

1. RECORD: a tiny agent loop runs against scripted providers; every LLM call
   and data read is appended to a chain-hashed trace.
2. REPLAY: the same loop runs again with boundaries that can only answer
   from the trace (attaching a live provider is a construction-time error).
3. PROOF: the replayed trace is byte-identical (equal chain hash).
"""

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

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
from tradewind.trace.reader import read_trace, verify_trace
from tradewind.trace.writer import TraceWriter


class ScriptedLLM:
    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content=f"HOLD — scripted response to {request.hash()[:8]}",
            prompt_tokens=42,
            completion_tokens=7,
            cost_estimate="0.0002",
        )


class ScriptedData:
    def read(self, request: DataRequest) -> DataResponse:
        return DataResponse(data={"symbol": request.params["symbol"], "close": "187.44"})


def drive(
    llm: LLMBoundary, data: DataBoundary, writer: TraceWriter, clock: VirtualClock, rand: SeededRand
) -> None:
    bars, bars_ev = data.read(DataRequest(source="bars", params={"symbol": "NVDA"}))
    decision, dec_ev = llm.complete(
        LLMRequest(
            model="scripted-model",
            messages=[{"role": "user", "content": f"decide on {bars.data} at {clock.now()}"}],
            temperature=0.0,
        ),
        parent_seq=bars_ev.seq,
    )
    clock.advance(timedelta(days=1))
    writer.append(
        "proposed_action",
        {
            "side": "hold",
            "symbol": "NVDA",
            "quantity": "0",
            "reason": decision.content,
            "jitter": rand.randint(1, 1000),
        },
        parent_seq=dec_ev.seq,
    )


def main() -> None:
    header = TraceHeader(
        config_hash="d" * 64,
        code_version="0.1.0",
        model_ids=["scripted-model"],
        submodule_sha=None,
        rng_seed=7,
    )
    with tempfile.TemporaryDirectory() as tmp:
        recorded = Path(tmp) / "recorded.jsonl"
        with TraceWriter(recorded, header) as writer:
            drive(
                LLMBoundary(Mode.RECORD, writer, provider=ScriptedLLM()),
                DataBoundary(Mode.RECORD, writer, provider=ScriptedData()),
                writer,
                VirtualClock(),
                SeededRand(7),
            )
        print(f"recorded : {verify_trace(recorded).final_chain_hash}")

        _, events = read_trace(recorded)
        index = ReplayIndex(events)
        replayed = Path(tmp) / "replayed.jsonl"
        with TraceWriter(replayed, header) as writer:
            drive(
                LLMBoundary(Mode.REPLAY, writer, replay_index=index),
                DataBoundary(Mode.REPLAY, writer, replay_index=index),
                writer,
                VirtualClock(),
                SeededRand(7),
            )
        print(f"replayed : {verify_trace(replayed).final_chain_hash}")

        identical = recorded.read_bytes() == replayed.read_bytes()
        print(f"byte-identical: {identical}")
        if not identical:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
