"""Report model, HTML/JSON rendering, and trace diff."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.helpers import make_header
from tradewind.invariants.domain import ProposedAction, RiskConfig, Side
from tradewind.report.diff import diff_traces
from tradewind.report.model import build_report_model
from tradewind.report.render import render_diff_html, render_report_html
from tradewind.report.svg import equity_svg
from tradewind.sim.agents import ScriptedAgent
from tradewind.sim.bars import load_price_data
from tradewind.sim.simulator import SimConfig, Simulator
from tradewind.trace.boundaries import LLMBoundary, LLMRequest, Mode
from tradewind.trace.writer import TraceWriter

DATA = Path(__file__).resolve().parent.parent / "benchmarks" / "data"
GOLDEN = DATA / "golden"


def _generous_risk(max_pos: str = "1000000") -> RiskConfig:
    return RiskConfig(
        max_position_per_symbol=Decimal(max_pos),
        max_gross_exposure=Decimal("100000000"),
        max_drawdown=Decimal("0.50"),
        price_sanity_pct=Decimal("0.10"),
        max_orders_per_window=1000,
        rate_window_seconds=86400,
    )


def _golden_trace(path: Path, max_pos: str = "1000000") -> None:
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    config = SimConfig(
        initial_cash=Decimal("10000"),
        universe=frozenset({"AAPL"}),
        risk=_generous_risk(max_pos),
    )
    agent = ScriptedAgent(
        {
            date(2024, 1, 2): [ProposedAction("AAPL", Side.BUY, Decimal("10"))],
            date(2024, 1, 3): [ProposedAction("AAPL", Side.SELL, Decimal("10"))],
        }
    )
    with TraceWriter(path, make_header()) as writer:
        Simulator(prices, config).run(agent, writer=writer)


def test_report_model_reconstructs_decisions(tmp_path: Path) -> None:
    trace = tmp_path / "t.jsonl"
    _golden_trace(trace)
    model = build_report_model(trace, initial_equity=Decimal("10000"))

    assert model.proposed == 2
    assert model.admitted == 2
    assert model.violation_count == 0
    assert model.replay_verified is True
    # Two fills → two equity points; last equals starting 10000 + 100 P&L.
    assert [p.value for p in model.equity_curve] == [Decimal("10000"), Decimal("10100")]
    assert not model.is_pnl  # initial_equity given → absolute


def test_report_pnl_mode_without_initial(tmp_path: Path) -> None:
    trace = tmp_path / "t.jsonl"
    _golden_trace(trace)
    model = build_report_model(trace)  # default: P&L from 0
    assert model.is_pnl
    assert model.equity_curve[-1].value == Decimal("100")


def test_report_captures_violations(tmp_path: Path) -> None:
    trace = tmp_path / "t.jsonl"
    _golden_trace(trace, max_pos="5")  # cap below order size → both orders vetoed
    model = build_report_model(trace)
    assert model.admitted == 0
    assert model.violation_count >= 1
    blocked = [d for d in model.decisions if d.blocked]
    assert blocked and blocked[0].violations


def test_report_html_is_self_contained(tmp_path: Path) -> None:
    trace = tmp_path / "t.jsonl"
    _golden_trace(trace)
    html = render_report_html(build_report_model(trace, initial_equity=Decimal("10000")))
    assert html.startswith("<!doctype html>")
    assert "<script" not in html
    assert "http://" not in html and "https://" not in html
    assert "Determinism attestation" in html
    assert "byte-identical" in html  # replay verified pill


def test_report_html_escapes_malicious_payload(tmp_path: Path) -> None:
    """Autoescaping: hostile text in a rendered field must not become live markup."""
    trace = tmp_path / "evil.jsonl"
    with TraceWriter(trace, make_header()) as writer:
        # The symbol is rendered in the decision table; it must be escaped.
        writer.append(
            "proposed_action",
            {"symbol": "<img src=x onerror=alert(1)>", "side": "buy", "quantity": "1"},
        )
    html = render_report_html(build_report_model(trace))
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img" in html  # rendered as inert text, not markup


def test_usage_totals_group_by_role(tmp_path: Path) -> None:
    class _P:
        def complete(self, request: LLMRequest) -> object:
            from tradewind.trace.boundaries import LLMResponse

            return LLMResponse(
                content="ok", prompt_tokens=10, completion_tokens=4, cost_estimate="0.05"
            )

    trace = tmp_path / "llm.jsonl"
    with TraceWriter(trace, make_header()) as writer:
        llm = LLMBoundary(Mode.RECORD, writer, provider=_P())
        llm.complete(
            LLMRequest(model="m", messages=[{"role": "user", "content": "a"}]), role="analyst"
        )
        llm.complete(
            LLMRequest(model="m", messages=[{"role": "user", "content": "b"}]), role="analyst"
        )
        llm.complete(
            LLMRequest(model="m", messages=[{"role": "user", "content": "c"}]), role="trader"
        )
    model = build_report_model(trace)
    by_role = {u.role: u for u in model.usage}
    assert by_role["analyst"].calls == 2
    assert by_role["analyst"].prompt_tokens == 20
    assert by_role["analyst"].cost == Decimal("0.10")
    assert by_role["trader"].calls == 1


def test_report_rejects_tampered_trace(tmp_path: Path) -> None:
    from tradewind.errors import TraceIntegrityError

    trace = tmp_path / "t.jsonl"
    _golden_trace(trace)
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(trace.read_bytes().replace(b'"AAPL"', b'"EVIL"', 1))
    with pytest.raises(TraceIntegrityError):
        build_report_model(tampered)


# --- diff ------------------------------------------------------------------


def test_diff_identical_traces(tmp_path: Path) -> None:
    a = tmp_path / "a.jsonl"
    _golden_trace(a)
    result = diff_traces(a, a)
    assert not result.diverged
    assert result.first_divergence_seq is None
    assert all(r.same for r in result.rows)


def test_diff_finds_first_divergence(tmp_path: Path) -> None:
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _golden_trace(a)  # buys then sells 10
    prices = load_price_data(GOLDEN, frozenset({"AAPL"}))
    config = SimConfig(
        initial_cash=Decimal("10000"), universe=frozenset({"AAPL"}), risk=_generous_risk()
    )
    other = ScriptedAgent({date(2024, 1, 2): [ProposedAction("AAPL", Side.BUY, Decimal("7"))]})
    with TraceWriter(b, make_header()) as writer:
        Simulator(prices, config).run(other, writer=writer)
    result = diff_traces(a, b)
    assert result.diverged
    assert result.first_divergence_seq == 1  # different quantity in the first action
    html = render_diff_html(result)
    assert "trace diff" in html and "<script" not in html


def test_equity_svg_degenerate_and_zero_crossing() -> None:
    from tradewind.report.model import EquityPoint

    assert "no fills to plot" in equity_svg([], is_pnl=True)
    one = equity_svg([EquityPoint("d0", Decimal("5"))], is_pnl=False)
    assert "<polyline" in one and "<script" not in one
    # A curve spanning negative to positive draws the zero baseline.
    crossing = equity_svg(
        [EquityPoint("a", Decimal("-10")), EquityPoint("b", Decimal("20"))], is_pnl=True
    )
    assert "chart-zero" in crossing


def test_model_to_json_is_serialisable(tmp_path: Path) -> None:
    import json

    trace = tmp_path / "t.jsonl"
    _golden_trace(trace)
    payload = build_report_model(trace, initial_equity=Decimal("10000")).to_json()
    # Round-trips through JSON (all values JSON-native, money as strings).
    restored = json.loads(json.dumps(payload))
    assert restored["totals"] == {"proposed": 2, "admitted": 2, "violations": 0}
    assert restored["attestation"]["replay_verified"] is True
    assert restored["equity_curve"]["points"][-1]["value"] == "10100.00"
    assert restored["decisions"][0]["fill"]["price"] == "110.00"


def test_diff_to_json(tmp_path: Path) -> None:
    import json

    a = tmp_path / "a.jsonl"
    _golden_trace(a)
    payload = diff_traces(a, a).to_json()
    restored = json.loads(json.dumps(payload))
    assert restored["diverged"] is False
    assert restored["rows"][0]["same"] is True


def test_provenance_links_action_to_agent_message(tmp_path: Path) -> None:
    """A proposed_action whose parent is an agent_message shows it as provenance."""
    trace = tmp_path / "prov.jsonl"
    with TraceWriter(trace, make_header()) as writer:
        msg = writer.append("agent_message", {"role": "trader", "content": "buy the dip"})
        writer.append(
            "proposed_action",
            {"symbol": "AAPL", "side": "buy", "quantity": "1"},
            parent_seq=msg.seq,
        )
    model = build_report_model(trace)
    prov = model.decisions[0].provenance
    assert len(prov) == 1
    assert prov[0]["event_type"] == "agent_message"
    assert prov[0]["role"] == "trader"
