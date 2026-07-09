"""MCP tool handlers and server assembly.

Contract: each handler is a plain, synchronous function over JSON-native
values — a thin shim onto the library API with no logic of its own beyond
shaping the result. Errors surface as the library's loud, specific exceptions
(``TraceIntegrityError``, ``ReplayDivergence``); the MCP layer reports them to
the client rather than swallowing them. :func:`create_server` lazily imports
the optional ``mcp`` package and registers every handler on a ``FastMCP``
instance; nothing else in Tradewind imports ``mcp``.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

from tradewind.errors import TradewindError


class McpDependencyMissing(TradewindError):
    """The optional ``mcp`` package is not installed."""


def verify_trace(trace_path: str) -> dict[str, Any]:
    """Verify a trace's structure and tamper-evident chain hash."""
    from tradewind.trace.reader import verify_trace as _verify

    result = _verify(Path(trace_path))
    return {
        "path": result.path,
        "event_count": result.event_count,
        "final_chain_hash": result.final_chain_hash,
        "verified": True,
    }


def replay_trace(trace_path: str) -> dict[str, Any]:
    """Replay a recorded trace with zero network calls; prove byte-identity."""
    from tradewind.trace.replay import replay_trace_file

    report = replay_trace_file(Path(trace_path))
    return {
        "source_path": report.source_path,
        "event_count": report.event_count,
        "final_chain_hash": report.final_chain_hash,
        "byte_identical": report.byte_identical,
    }


def get_report(trace_path: str, initial_cash: str | None = None) -> dict[str, Any]:
    """Build the full evaluation report for a trace as machine-readable JSON."""
    from tradewind.report.model import build_report_model

    model = build_report_model(
        Path(trace_path), Decimal(initial_cash) if initial_cash is not None else Decimal(0)
    )
    return model.to_json()


def list_invariants() -> list[dict[str, str]]:
    """List the v1 invariant pipeline: name and the formal property enforced."""
    from tradewind.invariants.checks import default_invariants
    from tradewind.invariants.domain import RiskConfig

    reference = RiskConfig(
        max_position_per_symbol=Decimal(1),
        max_gross_exposure=Decimal(1),
        max_drawdown=Decimal("0.1"),
        price_sanity_pct=Decimal("0.1"),
        max_orders_per_window=1,
        rate_window_seconds=60,
    )
    return [
        {
            "name": invariant.name,
            "property": (invariant.__class__.__doc__ or "").strip(),
        }
        for invariant in default_invariants(reference)
    ]


def run_evaluation(
    data_dir: str,
    symbol: str = "AAPL",
    agent: str = "sma",
    cash: str = "100000",
    quantity: str = "100",
    trace_out: str | None = None,
) -> dict[str, Any]:
    """Run a built-in scripted agent over bundled bars under the full harness.

    Mirrors ``tradewind run``: deterministic and offline. With ``trace_out``
    it also writes (and verifies) the chain-hashed decision trace.
    """
    from tradewind.invariants.domain import MarketModel, RiskConfig
    from tradewind.sim import SimConfig, Simulator, load_price_data
    from tradewind.sim.agents import BuyAndHold, SmaCrossover
    from tradewind.sim.simulator import Agent
    from tradewind.trace.canonical import canonical_json, sha256_hex
    from tradewind.trace.events import TraceHeader
    from tradewind.trace.writer import TraceWriter

    if agent not in ("sma", "buy_and_hold"):
        raise ValueError(f"unknown agent {agent!r}; expected 'sma' or 'buy_and_hold'")
    universe = frozenset({symbol})
    config = SimConfig(
        initial_cash=Decimal(cash),
        universe=universe,
        risk=RiskConfig(
            max_position_per_symbol=Decimal("100000"),
            max_gross_exposure=Decimal("100000000"),
            max_drawdown=Decimal("0.25"),
            price_sanity_pct=Decimal("0.10"),
            max_orders_per_window=100,
            rate_window_seconds=86400,
        ),
        model=MarketModel(fee_bps=Decimal("1"), slippage_bps=Decimal("5")),
    )
    chosen: Agent = (
        BuyAndHold(symbol, Decimal(quantity))
        if agent == "buy_and_hold"
        else SmaCrossover(symbol, Decimal(quantity))
    )
    simulator = Simulator(load_price_data(data_dir, universe), config)

    trace_info: dict[str, Any] | None = None
    if trace_out is None:
        result = simulator.run(chosen)
    else:
        header = TraceHeader(
            config_hash=sha256_hex(
                canonical_json({"agent": agent, "symbol": symbol, "cash": cash, "qty": quantity})
            ),
            code_version="0.1.0",
            model_ids=[f"scripted:{agent}"],
            rng_seed=0,
        )
        with TraceWriter(trace_out, header) as writer:
            result = simulator.run(chosen, writer=writer)
        trace_info = verify_trace(trace_out)

    return {
        "proposed": result.proposed,
        "admitted": result.admitted,
        "violations": result.violations,
        "final_equity": str(result.final_equity),
        "trace": trace_info,
    }


def create_server() -> Any:
    """Assemble the FastMCP server with every tool registered (needs ``mcp``)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise McpDependencyMissing(
            "the optional 'mcp' package is required for the MCP server; "
            "install with: pip install 'tradewind[mcp]'"
        ) from exc

    server = FastMCP(
        "tradewind",
        instructions=(
            "Deterministic verification harness for LLM trading agents: "
            "verify/replay chain-hashed traces, run scripted evaluations under "
            "the invariant engine, and fetch full evaluation reports."
        ),
    )
    for handler in (verify_trace, replay_trace, get_report, list_invariants, run_evaluation):
        server.tool()(handler)
    return server


def main() -> None:  # pragma: no cover - manual entry point
    """Run the MCP server over stdio (``python -m tradewind.mcp.server``)."""
    create_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
