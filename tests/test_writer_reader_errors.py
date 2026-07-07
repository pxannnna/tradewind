"""Writer and reader guard rails: closed writers, bad parent_seq, malformed lines."""

from pathlib import Path

import pytest

from tests.helpers import make_header
from tradewind.errors import TraceIntegrityError
from tradewind.trace.reader import verify_trace
from tradewind.trace.writer import TraceWriteError, TraceWriter


def test_append_to_closed_writer_raises(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "t.jsonl", make_header())
    writer.append("agent_message", {"role": "x", "content": "y"})
    writer.close()
    with pytest.raises(TraceWriteError, match="is closed"):
        writer.append("agent_message", {"role": "x", "content": "z"})


def test_forward_parent_seq_rejected(tmp_path: Path) -> None:
    # next_seq is 1, so parent_seq=1 (== seq) is a forward reference.
    with (
        TraceWriter(tmp_path / "t.jsonl", make_header()) as writer,
        pytest.raises(TraceWriteError, match="must precede"),
    ):
        writer.append("fill", {"x": 1}, parent_seq=1)


def test_writer_exposes_path_and_next_seq(tmp_path: Path) -> None:
    target = tmp_path / "t.jsonl"
    with TraceWriter(target, make_header()) as writer:
        assert writer.path == target
        assert writer.next_seq == 1
        writer.append("fill", {"x": 1})
        assert writer.next_seq == 2


def test_non_object_json_line_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    with TraceWriter(bad, make_header()):
        pass
    bad.write_bytes(bad.read_bytes() + b"[1,2,3]\n")
    with pytest.raises(TraceIntegrityError, match="expected a JSON object"):
        verify_trace(bad)


def test_forward_parent_seq_in_file_rejected(tmp_path: Path) -> None:
    """A hand-forged trace with parent_seq >= seq must fail verification."""
    from tradewind.trace.canonical import canonical_json, chain_hash
    from tradewind.trace.events import TraceEvent

    header = make_header()
    forged = tmp_path / "forged.jsonl"
    event = TraceEvent(seq=1, event_type="fill", parent_seq=1, payload={}, chain_hash="")
    # Give it a *valid* chain hash so we exercise the parent_seq check, not the hash check.
    body = event.model_dump(mode="json", exclude={"chain_hash"})
    linked = event.model_copy(
        update={"chain_hash": chain_hash(header.genesis_hash(), canonical_json(body))}
    )
    forged.write_bytes(
        canonical_json(header.model_dump(mode="json")) + b"\n" + linked.to_line() + b"\n"
    )
    with pytest.raises(TraceIntegrityError, match="does not precede"):
        verify_trace(forged)
