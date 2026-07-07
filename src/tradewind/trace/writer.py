"""Append-only trace writer.

Contract: a :class:`TraceWriter` owns one trace file for its lifetime. It
writes the header line on open, assigns strictly monotonic ``seq`` numbers,
maintains the SHA-256 chain hash, and only ever appends. It never opens an
existing file for writing (exclusive create), so a trace on disk is
immutable once written.
"""

from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from tradewind.errors import TradewindError
from tradewind.trace.canonical import canonical_json
from tradewind.trace.events import EventType, TraceEvent, TraceHeader


class TraceWriteError(TradewindError):
    """Attempted an invalid write (closed writer, bad parent_seq, existing file)."""


class TraceWriter:
    """Writes a header plus a chain-hashed stream of events to a JSONL file.

    Usable as a context manager. ``append`` returns the fully-formed
    :class:`TraceEvent` (with ``seq`` and ``chain_hash`` assigned) so callers
    can link later events to it via ``parent_seq``.
    """

    def __init__(self, path: Path | str, header: TraceHeader) -> None:
        self._path = Path(path)
        self.header = header
        try:
            self._fh: IO[bytes] = self._path.open("xb")
        except FileExistsError as exc:
            raise TraceWriteError(
                f"trace file already exists (traces are immutable): {self._path}"
            ) from exc
        self._fh.write(canonical_json(header.model_dump(mode="json")) + b"\n")
        self._prev_hash = header.genesis_hash()
        self._next_seq = 1
        self._closed = False

    @property
    def path(self) -> Path:
        """The trace file being written."""
        return self._path

    @property
    def last_chain_hash(self) -> str:
        """Chain hash of the most recently written record (header if none)."""
        return self._prev_hash

    @property
    def next_seq(self) -> int:
        """The ``seq`` the next appended event will receive."""
        return self._next_seq

    def append(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        parent_seq: int | None = None,
    ) -> TraceEvent:
        """Append one event, assigning its ``seq`` and chain hash; flush to disk."""
        if self._closed:
            raise TraceWriteError(f"writer for {self._path} is closed")
        seq = self._next_seq
        if parent_seq is not None and parent_seq >= seq:
            raise TraceWriteError(f"parent_seq {parent_seq} must precede seq {seq}")
        unhashed = TraceEvent(
            seq=seq,
            event_type=event_type,
            parent_seq=parent_seq,
            payload=payload,
            chain_hash="",
        )
        event = unhashed.model_copy(
            update={"chain_hash": unhashed.expected_chain_hash(self._prev_hash)}
        )
        self._fh.write(event.to_line() + b"\n")
        self._fh.flush()
        self._prev_hash = event.chain_hash
        self._next_seq = seq + 1
        return event

    def close(self) -> None:
        """Close the underlying file; further appends raise."""
        if not self._closed:
            self._fh.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
