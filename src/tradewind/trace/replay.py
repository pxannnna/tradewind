"""Trace-driven replay: re-execute a recorded event stream byte-identically.

Contract: :func:`replay_trace_file` verifies a trace, then re-executes its
event stream through the real writer path — every event is re-validated,
re-canonicalised, re-hashed, and re-serialised by the same code that wrote
the original. Success requires the replayed file to be byte-identical to
the recording (equal final chain hash and equal bytes); any difference
raises :class:`~tradewind.errors.ReplayDivergence`. No network, provider,
or clock is involved anywhere in this path.

Driver-based replay (re-running an actual agent program against recorded
boundary responses) is built from :class:`~tradewind.trace.boundaries` in
REPLAY mode; see ``tests/test_record_replay.py`` and ``examples/``.
"""

import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tradewind.errors import ReplayDivergence
from tradewind.trace.events import TraceEvent, TraceHeader
from tradewind.trace.reader import read_trace
from tradewind.trace.writer import TraceWriter


class ReplayReport(BaseModel):
    """Successful replay attestation."""

    model_config = ConfigDict(frozen=True)

    source_path: str
    replayed_path: str | None
    event_count: int
    final_chain_hash: str
    byte_identical: bool


def replay_trace_file(path: Path | str, out_path: Path | str | None = None) -> ReplayReport:
    """Replay ``path`` through the writer; prove the result is byte-identical.

    If ``out_path`` is given the replayed trace is kept there; otherwise it
    is written to a temporary file and discarded after comparison.
    """
    source = Path(path)
    header, events = read_trace(source)  # verifies integrity first

    if out_path is not None:
        return _replay_into(source, header, events, Path(out_path), keep=True)
    with tempfile.TemporaryDirectory(prefix="tradewind-replay-") as tmp:
        return _replay_into(source, header, events, Path(tmp) / "replay.jsonl", keep=False)


def _replay_into(
    source: Path,
    header: TraceHeader,
    events: list[TraceEvent],
    target: Path,
    keep: bool,
) -> ReplayReport:
    with TraceWriter(target, header) as writer:
        for event in events:
            replayed = writer.append(event.event_type, event.payload, event.parent_seq)
            if replayed.chain_hash != event.chain_hash:
                raise ReplayDivergence(
                    f"replay diverged at seq {event.seq}: recorded chain hash "
                    f"{event.chain_hash[:16]}…, replayed {replayed.chain_hash[:16]}…; "
                    "the serialisation code no longer reproduces this trace"
                )
        final = writer.last_chain_hash

    if source.read_bytes() != target.read_bytes():
        raise ReplayDivergence(
            f"replayed trace is not byte-identical to {source} despite matching "
            "chain hashes — file-level encoding changed"
        )
    return ReplayReport(
        source_path=str(source),
        replayed_path=str(target) if keep else None,
        event_count=len(events),
        final_chain_hash=final,
        byte_identical=True,
    )
