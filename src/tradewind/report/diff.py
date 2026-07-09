"""Aligned diff of two traces: find where their decisions first diverged.

Contract: both traces are fully verified on load. Events are aligned by ``seq``
and compared on ``(event_type, canonical payload)``. The result records the
first divergent ``seq`` (if any) and a row-by-row alignment, so a report can
show exactly where two runs — e.g. the same config under two models — parted
ways. Pure and side-effect-free.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tradewind.trace.canonical import canonical_json
from tradewind.trace.events import TraceEvent
from tradewind.trace.reader import read_trace


@dataclass(frozen=True)
class DiffRow:
    """One aligned position across the two traces."""

    seq: int
    same: bool
    left: str | None
    right: str | None

    def to_json(self) -> dict[str, object]:
        """JSON-native form of the row."""
        return {"seq": self.seq, "same": self.same, "left": self.left, "right": self.right}


@dataclass(frozen=True)
class DiffResult:
    """The outcome of diffing two traces."""

    left_path: str
    right_path: str
    diverged: bool
    first_divergence_seq: int | None
    rows: Sequence[DiffRow]
    left_count: int
    right_count: int

    def to_json(self) -> dict[str, object]:
        """Complete machine-readable diff."""
        return {
            "left_path": self.left_path,
            "right_path": self.right_path,
            "diverged": self.diverged,
            "first_divergence_seq": self.first_divergence_seq,
            "left_count": self.left_count,
            "right_count": self.right_count,
            "rows": [r.to_json() for r in self.rows],
        }


def _summary(event: TraceEvent) -> str:
    """Return a compact one-line view of an event for the diff table."""
    keys = ("symbol", "side", "quantity", "price", "blocked", "invariant", "content", "role")
    parts = [f"{k}={event.payload[k]}" for k in keys if k in event.payload]
    detail = " ".join(parts)
    return f"{event.event_type}({detail})" if detail else event.event_type


def _payload_key(event: TraceEvent) -> bytes:
    return canonical_json({"event_type": event.event_type, "payload": event.payload})


def diff_traces(left_path: Path | str, right_path: Path | str) -> DiffResult:
    """Verify and align two traces; report the first point of divergence."""
    _, left = read_trace(left_path)
    _, right = read_trace(right_path)
    by_left: dict[int, TraceEvent] = {e.seq: e for e in left}
    by_right: dict[int, TraceEvent] = {e.seq: e for e in right}
    all_seqs = sorted(set(by_left) | set(by_right))

    rows: list[DiffRow] = []
    first_divergence: int | None = None
    for seq in all_seqs:
        le = by_left.get(seq)
        re = by_right.get(seq)
        same = le is not None and re is not None and _payload_key(le) == _payload_key(re)
        if not same and first_divergence is None:
            first_divergence = seq
        rows.append(
            DiffRow(
                seq=seq,
                same=same,
                left=None if le is None else _summary(le),
                right=None if re is None else _summary(re),
            )
        )

    return DiffResult(
        left_path=str(left_path),
        right_path=str(right_path),
        diverged=first_divergence is not None,
        first_divergence_seq=first_divergence,
        rows=rows,
        left_count=len(left),
        right_count=len(right),
    )
