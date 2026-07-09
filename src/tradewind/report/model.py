"""Build a structured, JSON-serialisable report model from a verified trace.

Contract: :func:`build_report_model` first fully verifies the trace (via
:func:`read_trace`), then reconstructs the decision chain purely from the event
stream and its ``parent_seq`` links — it invents nothing the trace does not
contain. It also attempts a byte-identical replay to set the determinism
attestation flag. All monetary values in the model are exact Decimals and
serialise to decimal strings.

Equity curve: the trace records fills, not per-bar marks, so the curve is the
cumulative change in portfolio value *at fill prices* — each position marked at
the most recent price it traded. With ``initial_equity`` supplied the curve is
absolute equity; otherwise it is P&L from zero. This is stated in the report so
the marks-only nature is never oversold.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradewind.errors import ReplayDivergence, TraceIntegrityError
from tradewind.invariants.domain import Fill, Side
from tradewind.money import ZERO, decimal_str, to_decimal
from tradewind.trace.events import TraceEvent, TraceHeader
from tradewind.trace.reader import read_trace
from tradewind.trace.replay import replay_trace_file


@dataclass(frozen=True)
class LlmUsage:
    """Token and cost totals for one agent role (or model, if untagged)."""

    role: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost: Decimal

    def to_json(self) -> dict[str, object]:
        """JSON-native form (cost as a decimal string)."""
        return {
            "role": self.role,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost": decimal_str(self.cost),
        }


@dataclass(frozen=True)
class DecisionRow:
    """One proposed action and everything the harness did with it."""

    action_seq: int
    action: Mapping[str, Any]
    blocked: bool
    verdicts: Sequence[Mapping[str, Any]]
    violations: Sequence[Mapping[str, Any]]
    fill: Mapping[str, Any] | None
    provenance: Sequence[Mapping[str, Any]]

    def to_json(self) -> dict[str, object]:
        """JSON-native form of the decision row."""
        return {
            "action_seq": self.action_seq,
            "action": dict(self.action),
            "blocked": self.blocked,
            "verdicts": [dict(v) for v in self.verdicts],
            "violations": [dict(v) for v in self.violations],
            "fill": None if self.fill is None else dict(self.fill),
            "provenance": [dict(p) for p in self.provenance],
        }


@dataclass(frozen=True)
class EquityPoint:
    """A point on the P&L / equity curve."""

    label: str
    value: Decimal


@dataclass(frozen=True)
class ReportModel:
    """The full report, ready to render to HTML or JSON."""

    header: TraceHeader
    event_count: int
    chain_hash: str
    replay_verified: bool
    decisions: Sequence[DecisionRow]
    violations: Sequence[Mapping[str, Any]]
    usage: Sequence[LlmUsage]
    equity_curve: Sequence[EquityPoint]
    is_pnl: bool
    proposed: int
    admitted: int
    violation_count: int
    extra_events: Sequence[Mapping[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        """Complete machine-readable report."""
        return {
            "header": self.header.model_dump(mode="json"),
            "attestation": {
                "event_count": self.event_count,
                "chain_hash": self.chain_hash,
                "replay_verified": self.replay_verified,
            },
            "totals": {
                "proposed": self.proposed,
                "admitted": self.admitted,
                "violations": self.violation_count,
            },
            "decisions": [d.to_json() for d in self.decisions],
            "violations": [dict(v) for v in self.violations],
            "usage": [u.to_json() for u in self.usage],
            "equity_curve": {
                "is_pnl": self.is_pnl,
                "points": [
                    {"label": p.label, "value": decimal_str(p.value)} for p in self.equity_curve
                ],
            },
        }


def _children(events: Sequence[TraceEvent]) -> dict[int, list[TraceEvent]]:
    by_parent: dict[int, list[TraceEvent]] = {}
    for event in events:
        if event.parent_seq is not None:
            by_parent.setdefault(event.parent_seq, []).append(event)
    return by_parent


def _provenance(event: TraceEvent, by_seq: Mapping[int, TraceEvent]) -> list[dict[str, Any]]:
    """Walk ``parent_seq`` upward, collecting agent_message / llm_call ancestors."""
    chain: list[dict[str, Any]] = []
    current = event.parent_seq
    seen: set[int] = set()
    while current is not None and current not in seen:
        seen.add(current)
        parent = by_seq.get(current)
        if parent is None:
            break
        if parent.event_type in ("agent_message", "llm_call"):
            chain.append({"seq": parent.seq, "event_type": parent.event_type, **parent.payload})
        current = parent.parent_seq
    chain.reverse()
    return chain


def _usage(events: Sequence[TraceEvent]) -> list[LlmUsage]:
    """Aggregate llm_call token/cost totals, grouped by role (falling back to model)."""
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type != "llm_call":
            continue
        payload = event.payload
        request = payload.get("request", {})
        role = str(payload.get("role") or request.get("model") or "unattributed")
        bucket = buckets.setdefault(role, {"calls": 0, "prompt": 0, "completion": 0, "cost": ZERO})
        bucket["calls"] += 1
        tokens = payload.get("token_counts", {})
        bucket["prompt"] += int(tokens.get("prompt", 0))
        bucket["completion"] += int(tokens.get("completion", 0))
        cost = payload.get("cost_estimate")
        if cost is not None:
            bucket["cost"] += to_decimal(str(cost))
    return [
        LlmUsage(role, b["calls"], b["prompt"], b["completion"], b["cost"])
        for role, b in sorted(buckets.items())
    ]


def _equity_curve(
    decisions: Sequence[DecisionRow], initial_equity: Decimal
) -> tuple[list[EquityPoint], bool]:
    """Cumulative value at fill prices; absolute if initial_equity given, else P&L."""
    positions: dict[str, Decimal] = {}
    last_price: dict[str, Decimal] = {}
    cash_delta_total = ZERO
    points: list[EquityPoint] = []
    for row in decisions:
        if row.fill is None:
            continue
        fill = row.fill
        symbol = str(fill["symbol"])
        positions[symbol] = positions.get(symbol, ZERO) + to_decimal(str(fill["position_delta"]))
        last_price[symbol] = to_decimal(str(fill["price"]))
        cash_delta_total += to_decimal(str(fill["cash_delta"]))
        marked = sum((positions[s] * last_price[s] for s in positions), ZERO)
        value = initial_equity + cash_delta_total + marked
        label = str(row.action.get("executed_on") or f"seq{row.action_seq}")
        points.append(EquityPoint(label=label, value=value))
    return points, initial_equity == ZERO


def build_report_model(path: Path | str, initial_equity: Decimal = ZERO) -> ReportModel:
    """Verify ``path`` and reconstruct the full report model from its events."""
    header, events = read_trace(path)
    by_seq = {e.seq: e for e in events}
    by_parent = _children(events)

    replay_verified = False
    try:
        replay_verified = replay_trace_file(path).byte_identical
    except (ReplayDivergence, TraceIntegrityError):
        replay_verified = False

    decisions: list[DecisionRow] = []
    proposed = admitted = 0
    for event in events:
        if event.event_type != "proposed_action":
            continue
        proposed += 1
        checks = [c for c in by_parent.get(event.seq, []) if c.event_type == "invariant_check"]
        check = checks[0] if checks else None
        verdicts: list[Mapping[str, Any]] = []
        violations: list[Mapping[str, Any]] = []
        fill: Mapping[str, Any] | None = None
        blocked = False
        if check is not None:
            blocked = bool(check.payload.get("blocked", False))
            verdicts = list(check.payload.get("verdicts", []))
            for child in by_parent.get(check.seq, []):
                if child.event_type == "violation":
                    violations.append(child.payload)
                elif child.event_type == "fill":
                    fill = child.payload
        if fill is not None:
            admitted += 1
        decisions.append(
            DecisionRow(
                action_seq=event.seq,
                action=event.payload,
                blocked=blocked,
                verdicts=verdicts,
                violations=violations,
                fill=fill,
                provenance=_provenance(event, by_seq),
            )
        )

    all_violations = [e.payload for e in events if e.event_type == "violation"]
    curve, is_pnl = _equity_curve(decisions, initial_equity)
    return ReportModel(
        header=header,
        event_count=len(events),
        chain_hash=events[-1].chain_hash if events else header.genesis_hash(),
        replay_verified=replay_verified,
        decisions=decisions,
        violations=all_violations,
        usage=_usage(events),
        equity_curve=curve,
        is_pnl=is_pnl,
        proposed=proposed,
        admitted=admitted,
        violation_count=len(all_violations),
    )


def fill_from_payload(payload: Mapping[str, Any]) -> Fill:
    """Reconstruct a :class:`Fill` from a fill event payload (used by tests/tools)."""
    return Fill(
        symbol=str(payload["symbol"]),
        side=Side(payload["side"]),
        quantity=to_decimal(str(payload["quantity"])),
        price=to_decimal(str(payload["price"])),
        fees=to_decimal(str(payload["fees"])),
        cash_delta=to_decimal(str(payload["cash_delta"])),
        position_delta=to_decimal(str(payload["position_delta"])),
    )
