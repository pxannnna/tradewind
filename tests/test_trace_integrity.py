"""Acceptance: any mutation of a trace fails verification with a clear error."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tradewind.errors import TraceIntegrityError
from tradewind.trace.reader import read_trace, verify_trace


def test_intact_trace_verifies(sample_trace: Path) -> None:
    result = verify_trace(sample_trace)
    assert result.event_count == 5
    assert len(result.final_chain_hash) == 64


def test_flipping_one_payload_byte_fails_chain_hash(sample_trace: Path, tmp_path: Path) -> None:
    raw = sample_trace.read_bytes()
    # Corrupt a value inside an event payload without breaking JSON syntax.
    assert b'"symbol":"AAPL"' in raw
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(raw.replace(b'"symbol":"AAPL"', b'"symbol":"AAPZ"', 1))
    with pytest.raises(TraceIntegrityError, match="chain-hash mismatch at seq"):
        verify_trace(tampered)


def test_editing_stored_chain_hash_fails(sample_trace: Path, tmp_path: Path) -> None:
    _, events = read_trace(sample_trace)
    raw = sample_trace.read_bytes()
    old = events[0].chain_hash.encode("ascii")
    new = (("0" if events[0].chain_hash[0] != "0" else "1") + events[0].chain_hash[1:]).encode()
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(raw.replace(old, new, 1))
    with pytest.raises(TraceIntegrityError, match="chain-hash mismatch"):
        verify_trace(tampered)


def test_deleting_a_middle_line_fails(sample_trace: Path, tmp_path: Path) -> None:
    lines = sample_trace.read_bytes().splitlines(keepends=True)
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(b"".join(lines[:2] + lines[3:]))  # drop event seq=2
    with pytest.raises(TraceIntegrityError, match="out of order"):
        verify_trace(tampered)


def test_reordering_lines_fails(sample_trace: Path, tmp_path: Path) -> None:
    lines = sample_trace.read_bytes().splitlines(keepends=True)
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(b"".join([lines[0], lines[2], lines[1], *lines[3:]]))
    with pytest.raises(TraceIntegrityError):
        verify_trace(tampered)


def test_non_canonical_reencoding_fails(sample_trace: Path, tmp_path: Path) -> None:
    # Same JSON value, different bytes (added space) — must be rejected.
    lines = sample_trace.read_bytes().splitlines(keepends=True)
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(
        lines[0] + lines[1].replace(b'"record":', b'"record": ', 1) + b"".join(lines[2:])
    )
    with pytest.raises(TraceIntegrityError, match="not canonical"):
        verify_trace(tampered)


def test_empty_file_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    with pytest.raises(TraceIntegrityError, match="empty file"):
        verify_trace(empty)


def test_error_message_names_file_and_line(sample_trace: Path, tmp_path: Path) -> None:
    raw = sample_trace.read_bytes()
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(raw.replace(b'"symbol":"AAPL"', b'"symbol":"AAPZ"', 1))
    with pytest.raises(TraceIntegrityError) as excinfo:
        verify_trace(tampered)
    message = str(excinfo.value)
    assert "tampered.jsonl" in message
    assert "seq" in message


@settings(max_examples=200)
@given(data=st.data())
def test_flipping_any_single_byte_fails(
    tmp_path_factory: pytest.TempPathFactory, data: st.DataObject
) -> None:
    """Property: flip ANY byte anywhere in the file → TraceIntegrityError."""
    from tests.helpers import record_sample_trace

    base = tmp_path_factory.mktemp("prop")
    recorded = base / "trace.jsonl"
    if not recorded.exists():
        record_sample_trace(recorded)
    raw = bytearray(recorded.read_bytes())
    index = data.draw(st.integers(min_value=0, max_value=len(raw) - 1))
    delta = data.draw(st.integers(min_value=1, max_value=255))
    raw[index] = (raw[index] + delta) % 256
    tampered = base / f"tampered-{index}-{delta}.jsonl"
    tampered.write_bytes(bytes(raw))
    with pytest.raises(TraceIntegrityError):
        verify_trace(tampered)
    tampered.unlink()
