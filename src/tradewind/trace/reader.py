"""Trace reading and tamper-evidence verification.

Contract: :func:`verify_trace` accepts a path and either returns a
:class:`TraceVerification` summary or raises
:class:`~tradewind.errors.TraceIntegrityError` naming the first offending
line. Verification is exhaustive:

1. every line must parse as canonical JSON into a valid record,
2. every line's bytes must equal the canonical re-serialisation of the
   parsed record (no alternate encodings),
3. ``seq`` must run 1, 2, 3, … and ``parent_seq`` must point backwards,
4. every stored ``chain_hash`` must equal the recomputed chain link.

:func:`read_trace` is verification plus materialisation — there is no way
to read a trace through this module without verifying it.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from tradewind.errors import TraceIntegrityError
from tradewind.trace.events import TraceEvent, TraceHeader


class TraceVerification(BaseModel):
    """Successful verification summary."""

    model_config = ConfigDict(frozen=True)

    path: str
    event_count: int
    final_chain_hash: str


def _parse_line(raw: bytes, line_no: int, path: Path) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise TraceIntegrityError(
            f"{path}:{line_no}: line is not valid UTF-8; the trace is corrupt"
        ) from exc
    except json.JSONDecodeError as exc:
        raise TraceIntegrityError(
            f"{path}:{line_no}: line is not valid JSON ({exc.msg}); "
            "the trace is corrupt or was edited"
        ) from exc
    if not isinstance(parsed, dict):
        raise TraceIntegrityError(f"{path}:{line_no}: expected a JSON object")
    return parsed


def _load_records(path: Path) -> tuple[TraceHeader, list[TraceEvent]]:
    """Parse and structurally validate all lines (steps 1–3 of the contract)."""
    raw_lines = path.read_bytes().split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    if not raw_lines:
        raise TraceIntegrityError(f"{path}: empty file, expected a header line")

    try:
        header = TraceHeader.model_validate(_parse_line(raw_lines[0], 1, path))
    except ValidationError as exc:
        raise TraceIntegrityError(f"{path}:1: invalid trace header: {exc}") from exc

    events: list[TraceEvent] = []
    for idx, raw in enumerate(raw_lines[1:], start=2):
        try:
            event = TraceEvent.model_validate(_parse_line(raw, idx, path))
        except ValidationError as exc:
            raise TraceIntegrityError(f"{path}:{idx}: invalid event record: {exc}") from exc
        if raw != event.to_line():
            raise TraceIntegrityError(
                f"{path}:{idx}: line bytes are not canonical JSON for the record "
                "(re-encoded or edited in place)"
            )
        expected_seq = idx - 1
        if event.seq != expected_seq:
            raise TraceIntegrityError(
                f"{path}:{idx}: seq {event.seq} out of order, expected {expected_seq}"
            )
        if event.parent_seq is not None and event.parent_seq >= event.seq:
            raise TraceIntegrityError(
                f"{path}:{idx}: parent_seq {event.parent_seq} does not precede seq {event.seq}"
            )
        events.append(event)
    return header, events


def read_trace(path: Path | str) -> tuple[TraceHeader, list[TraceEvent]]:
    """Read and fully verify a trace, returning its header and events."""
    p = Path(path)
    header, events = _load_records(p)
    _verify_chain(p, header, events)
    return header, events


def verify_trace(path: Path | str) -> TraceVerification:
    """Verify a trace end-to-end; raise :class:`TraceIntegrityError` on any defect."""
    p = Path(path)
    header, events = _load_records(p)
    final = _verify_chain(p, header, events)
    return TraceVerification(path=str(p), event_count=len(events), final_chain_hash=final)


def _verify_chain(path: Path, header: TraceHeader, events: list[TraceEvent]) -> str:
    """Recompute the chain (step 4); return the final chain hash."""
    prev = header.genesis_hash()
    for event in events:
        expected = event.expected_chain_hash(prev)
        if event.chain_hash != expected:
            raise TraceIntegrityError(
                f"{path}: chain-hash mismatch at seq {event.seq} "
                f"(line {event.seq + 1}): stored {event.chain_hash[:16]}…, "
                f"recomputed {expected[:16]}…; the trace was modified or truncated "
                "upstream of this event"
            )
        prev = event.chain_hash
    return prev
