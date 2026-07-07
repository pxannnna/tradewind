"""Recordable boundaries: every side effect an agent run can perform.

Contract: core code never calls an LLM SDK, a data API, the wall clock, or
global ``random`` directly. It goes through these four boundaries, each of
which is constructed in exactly one of two modes:

* **RECORD** — forward to a live provider, append the full request/response
  (plus latency, token counts, cost) to the trace.
* **REPLAY** — answer from the recorded trace, keyed by
  ``request_hash = sha256(canonical_json(request))``, re-emitting the
  recorded event payload verbatim. A cache miss raises
  :class:`~tradewind.errors.ReplayDivergence`; a live provider cannot even
  be attached in replay mode, so a silent live call is structurally
  impossible.

Repeated identical requests replay in first-recorded-first-replayed order
(per-hash FIFO), so a run that makes the same call twice gets the two
recorded responses in their original order.
"""

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import Enum
from random import Random
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from tradewind.errors import ReplayDivergence, TradewindError
from tradewind.trace.canonical import canonical_json, request_hash
from tradewind.trace.events import TraceEvent
from tradewind.trace.wallclock import WallTimer
from tradewind.trace.writer import TraceWriter


def _as_stored(payload: dict[str, Any]) -> dict[str, Any]:
    """Round-trip a record-mode payload through canonical JSON.

    Determinism contract: a boundary in RECORD mode must return the *same*
    object a REPLAY of the same trace would reconstruct. Replay reads its
    payload from the parsed trace line, where canonicalisation has already
    sorted every nested object's keys. Passing the freshly-built payload
    through the identical round-trip here means a driver that consumes the
    response (e.g. formats a dict into a prompt) sees byte-identical inputs
    in both modes, so record and replay cannot diverge on key ordering.
    """
    return cast(dict[str, Any], json.loads(canonical_json(payload).decode("utf-8")))


class Mode(Enum):
    """Boundary operating mode."""

    RECORD = "record"
    REPLAY = "replay"


class BoundaryConfigError(TradewindError):
    """A boundary was constructed with an invalid mode/provider combination."""


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class LLMRequest(BaseModel):
    """A provider-agnostic LLM call. All fields participate in the request hash."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    def hash(self) -> str:
        """Return the replay lookup key for this request."""
        return request_hash(self.model_dump(mode="json"))


class LLMResponse(BaseModel):
    """An LLM completion. ``cost_estimate`` is a decimal string (money is never float)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_estimate: str | None = None
    raw: dict[str, Any] | None = None


class DataRequest(BaseModel):
    """A market-data read (e.g. OHLCV bars for a symbol/date range)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    params: dict[str, Any] = Field(default_factory=dict)

    def hash(self) -> str:
        """Return the replay lookup key for this request."""
        return request_hash(self.model_dump(mode="json"))


class DataResponse(BaseModel):
    """A market-data result; ``data`` must be JSON-native (prices as decimal strings)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: Any


class LLMProvider(Protocol):
    """A live LLM backend (only ever attached in RECORD mode)."""

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute the call against the real provider."""
        ...


class DataProvider(Protocol):
    """A live market-data backend (only ever attached in RECORD mode)."""

    def read(self, request: DataRequest) -> DataResponse:
        """Execute the read against the real source."""
        ...


# ---------------------------------------------------------------------------
# Replay index
# ---------------------------------------------------------------------------


class ReplayIndex:
    """Recorded boundary events keyed by ``(event_type, request_hash)``, FIFO per key."""

    def __init__(self, events: list[TraceEvent]) -> None:
        self._queues: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        for event in events:
            if event.event_type in ("llm_call", "data_read"):
                key = (event.event_type, str(event.payload["request_hash"]))
                self._queues.setdefault(key, deque()).append(event.payload)

    def lookup(self, event_type: str, rhash: str) -> dict[str, Any]:
        """Pop the next recorded payload for this request; miss is a hard error."""
        queue = self._queues.get((event_type, rhash))
        if queue is None:
            raise ReplayDivergence(
                f"replay miss: no recorded {event_type} with request_hash "
                f"{rhash[:16]}…; the replayed code issued a request the "
                "recording never made (nondeterministic prompt, changed "
                "config, or wrong trace)"
            )
        if not queue:
            raise ReplayDivergence(
                f"replay exhausted: every recorded {event_type} with "
                f"request_hash {rhash[:16]}… was already consumed; the "
                "replayed code repeated this request more times than the "
                "recording did"
            )
        return queue.popleft()

    def pending(self) -> int:
        """Return the count of recorded boundary responses not yet consumed."""
        return sum(len(q) for q in self._queues.values())


# ---------------------------------------------------------------------------
# LLM / data boundaries
# ---------------------------------------------------------------------------


class LLMBoundary:
    """Intercepts every LLM call (see module docstring for the mode contract)."""

    def __init__(
        self,
        mode: Mode,
        writer: TraceWriter,
        provider: LLMProvider | None = None,
        replay_index: ReplayIndex | None = None,
        wall_timer: WallTimer | None = None,
    ) -> None:
        if mode is Mode.RECORD and provider is None:
            raise BoundaryConfigError("RECORD mode requires a live provider")
        if mode is Mode.REPLAY and provider is not None:
            raise BoundaryConfigError(
                "REPLAY mode must not have a live provider attached; "
                "replay answers only from the trace"
            )
        if mode is Mode.REPLAY and replay_index is None:
            raise BoundaryConfigError("REPLAY mode requires a replay index")
        self._mode = mode
        self._writer = writer
        self._provider = provider
        self._index = replay_index
        self._wall = wall_timer if wall_timer is not None else WallTimer()

    def complete(
        self, request: LLMRequest, parent_seq: int | None = None
    ) -> tuple[LLMResponse, TraceEvent]:
        """Run (or replay) one LLM call; append its ``llm_call`` event."""
        rhash = request.hash()
        if self._mode is Mode.RECORD:
            assert self._provider is not None
            marker = self._wall.start()
            live = self._provider.complete(request)
            latency_ms = self._wall.elapsed_ms(marker)
            payload: dict[str, Any] = _as_stored(
                {
                    "request_hash": rhash,
                    "request": request.model_dump(mode="json"),
                    "response": live.model_dump(mode="json"),
                    "latency_ms": latency_ms,
                    "token_counts": {
                        "prompt": live.prompt_tokens,
                        "completion": live.completion_tokens,
                    },
                    "cost_estimate": live.cost_estimate,
                }
            )
        else:
            assert self._index is not None
            payload = self._index.lookup("llm_call", rhash)
        response = LLMResponse.model_validate(payload["response"])
        event = self._writer.append("llm_call", payload, parent_seq)
        return response, event


class DataBoundary:
    """Intercepts every market-data read (same contract as :class:`LLMBoundary`)."""

    def __init__(
        self,
        mode: Mode,
        writer: TraceWriter,
        provider: DataProvider | None = None,
        replay_index: ReplayIndex | None = None,
    ) -> None:
        if mode is Mode.RECORD and provider is None:
            raise BoundaryConfigError("RECORD mode requires a live provider")
        if mode is Mode.REPLAY and provider is not None:
            raise BoundaryConfigError(
                "REPLAY mode must not have a live provider attached; "
                "replay answers only from the trace"
            )
        if mode is Mode.REPLAY and replay_index is None:
            raise BoundaryConfigError("REPLAY mode requires a replay index")
        self._mode = mode
        self._writer = writer
        self._provider = provider
        self._index = replay_index

    def read(
        self, request: DataRequest, parent_seq: int | None = None
    ) -> tuple[DataResponse, TraceEvent]:
        """Run (or replay) one data read; append its ``data_read`` event."""
        rhash = request.hash()
        if self._mode is Mode.RECORD:
            assert self._provider is not None
            live = self._provider.read(request)
            payload: dict[str, Any] = _as_stored(
                {
                    "request_hash": rhash,
                    "request": request.model_dump(mode="json"),
                    "response": live.model_dump(mode="json"),
                }
            )
        else:
            assert self._index is not None
            payload = self._index.lookup("data_read", rhash)
        response = DataResponse.model_validate(payload["response"])
        event = self._writer.append("data_read", payload, parent_seq)
        return response, event


# ---------------------------------------------------------------------------
# Clock / RNG boundaries
# ---------------------------------------------------------------------------

_DEFAULT_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class VirtualClock:
    """Deterministic clock: time moves only when the harness advances it.

    All agent-visible time comes from here in both record and replay mode,
    so 'now' is part of the run config, not an ambient side effect.
    """

    def __init__(self, start: datetime = _DEFAULT_EPOCH) -> None:
        if start.tzinfo is None:
            raise BoundaryConfigError("VirtualClock start must be timezone-aware")
        self._current = start

    def now(self) -> datetime:
        """Return the current virtual time (tz-aware)."""
        return self._current

    def advance(self, delta: timedelta) -> datetime:
        """Move virtual time forward by ``delta`` (must be non-negative)."""
        if delta < timedelta(0):
            raise BoundaryConfigError("virtual time cannot move backwards")
        self._current = self._current + delta
        return self._current

    def set_time(self, moment: datetime) -> datetime:
        """Jump to ``moment`` (must be tz-aware and >= current time)."""
        if moment.tzinfo is None:
            raise BoundaryConfigError("VirtualClock time must be timezone-aware")
        if moment < self._current:
            raise BoundaryConfigError("virtual time cannot move backwards")
        self._current = moment
        return self._current


class SeededRand:
    """Deterministic RNG: a :class:`random.Random` isolated from global state."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._rng = Random(seed)

    def random(self) -> float:
        """Uniform float in [0, 1)."""
        return self._rng.random()

    def randint(self, a: int, b: int) -> int:
        """Uniform integer in [a, b]."""
        return self._rng.randint(a, b)

    def choice(self, seq: list[Any]) -> Any:
        """Uniformly chosen element of a non-empty list."""
        return self._rng.choice(seq)

    def uniform(self, a: float, b: float) -> float:
        """Uniform float in [a, b]."""
        return self._rng.uniform(a, b)
