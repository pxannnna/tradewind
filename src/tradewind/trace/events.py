r"""Pydantic-validated trace records.

Contract: a trace file is one :class:`TraceHeader` line followed by zero or
more :class:`TraceEvent` lines, each serialised as canonical JSON (one per
line, ``\\n``-terminated). Every field in these models must be
JSON-native and deliberately excludes wall-clock timestamps: two runs of
the same program over the same recording must serialise byte-identically.

Chain hashing: ``hash_0 = sha256(canonical_json(header))`` and
``hash_n = sha256(hex(hash_{n-1}) || canonical_json(event_n without
chain_hash))``. The stored ``chain_hash`` on each event is that link's
value; verification recomputes the chain from scratch.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tradewind.trace.canonical import canonical_json, chain_hash, sha256_hex

TRACE_FORMAT_VERSION = 1

EventType = Literal[
    "llm_call",
    "data_read",
    "agent_message",
    "proposed_action",
    "invariant_check",
    "fill",
    "violation",
]

EVENT_TYPES: tuple[EventType, ...] = (
    "llm_call",
    "data_read",
    "agent_message",
    "proposed_action",
    "invariant_check",
    "fill",
    "violation",
)


class TraceHeader(BaseModel):
    """First line of every trace: identifies the run deterministically.

    Deliberately contains no timestamp — a replay of the run must be able
    to reproduce this header byte-for-byte.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record: Literal["header"] = "header"
    trace_format_version: int = TRACE_FORMAT_VERSION
    config_hash: str
    code_version: str
    model_ids: list[str] = Field(default_factory=list)
    submodule_sha: str | None = None
    rng_seed: int

    def genesis_hash(self) -> str:
        """Return ``hash_0``, the chain anchor: sha256 of the canonical header."""
        return sha256_hex(canonical_json(self.model_dump(mode="json")))


class TraceEvent(BaseModel):
    """One event line in a trace.

    ``seq`` is 1-based and strictly monotonic within a file. ``parent_seq``
    records causality (which earlier event triggered this one) and must be
    strictly less than ``seq``. ``payload`` is event-type-specific and must
    be JSON-native (money as decimal strings, never floats).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record: Literal["event"] = "event"
    seq: int = Field(ge=1)
    event_type: EventType
    parent_seq: int | None = Field(default=None, ge=1)
    payload: dict[str, Any]
    chain_hash: str

    def body_bytes(self) -> bytes:
        """Canonical JSON of everything the chain hash covers (all but chain_hash)."""
        body = self.model_dump(mode="json", exclude={"chain_hash"})
        return canonical_json(body)

    def expected_chain_hash(self, prev_hash_hex: str) -> str:
        """Recompute this event's chain link from the previous one."""
        return chain_hash(prev_hash_hex, self.body_bytes())

    def to_line(self) -> bytes:
        """Serialise to the exact bytes stored in the trace file (no newline)."""
        return canonical_json(self.model_dump(mode="json"))
