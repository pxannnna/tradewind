"""Tradewind command-line interface.

Contract: a thin, deterministic shell over the library API. Commands exit
0 on success and 1 on any verification/replay failure, printing the
specific error. Commands for later phases exist as stubs that exit 2 so
scripts fail loudly rather than silently no-op.
"""

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from tradewind.errors import ReplayDivergence, TraceIntegrityError
from tradewind.trace.reader import verify_trace
from tradewind.trace.replay import replay_trace_file

app = typer.Typer(
    name="tradewind",
    help="Deterministic verification harness for LLM trading agents.",
    no_args_is_help=True,
    add_completion=False,
)

_TracePath = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]


@app.command()
def verify(trace: _TracePath) -> None:
    """Verify a trace's structure and tamper-evident chain hash."""
    try:
        result = verify_trace(trace)
    except TraceIntegrityError as exc:
        typer.echo(f"FAIL: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"OK: {result.event_count} events, chain hash {result.final_chain_hash}")


@app.command()
def replay(
    trace: _TracePath,
    out: Annotated[
        Path | None,
        typer.Option(help="Keep the replayed trace at this path (must not exist)."),
    ] = None,
) -> None:
    """Replay a recorded trace with zero network calls; prove byte-identity."""
    try:
        report = replay_trace_file(trace, out)
    except (TraceIntegrityError, ReplayDivergence) as exc:
        typer.echo(f"FAIL: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"REPLAY OK: {report.event_count} events byte-identical, "
        f"chain hash {report.final_chain_hash}"
    )
    if report.replayed_path is not None:
        typer.echo(f"replayed trace written to {report.replayed_path}")


class AgentChoice(StrEnum):
    """Built-in scripted agents selectable from the CLI."""

    buy_and_hold = "buy_and_hold"
    sma = "sma"


@app.command()
def run(
    data: Annotated[Path, typer.Option(exists=True, file_okay=False, help="OHLCV data dir")],
    symbol: Annotated[str, typer.Option(help="Symbol to trade")] = "AAPL",
    agent: Annotated[AgentChoice, typer.Option(help="Built-in scripted agent")] = (AgentChoice.sma),
    cash: Annotated[str, typer.Option(help="Initial cash")] = "100000",
    quantity: Annotated[str, typer.Option(help="Order size in shares")] = "100",
    out: Annotated[
        Path | None, typer.Option(help="Write the decision trace here (must not exist)")
    ] = None,
) -> None:
    """Replay a built-in scripted agent over bundled bars under the harness.

    Deterministic and offline: no LLM, no network. With ``--out`` it writes a
    chain-hashed decision trace and verifies it before exiting.
    """
    from tradewind.invariants.domain import MarketModel, RiskConfig
    from tradewind.sim import SimConfig, Simulator, load_price_data
    from tradewind.sim.agents import BuyAndHold, SmaCrossover
    from tradewind.sim.simulator import Agent
    from tradewind.trace.canonical import canonical_json, sha256_hex
    from tradewind.trace.events import TraceHeader
    from tradewind.trace.writer import TraceWriter

    universe = frozenset({symbol})
    risk = RiskConfig(
        max_position_per_symbol=Decimal("100000"),
        max_gross_exposure=Decimal("100000000"),
        max_drawdown=Decimal("0.25"),
        price_sanity_pct=Decimal("0.10"),
        max_orders_per_window=100,
        rate_window_seconds=86400,
    )
    config = SimConfig(
        initial_cash=Decimal(cash),
        universe=universe,
        risk=risk,
        model=MarketModel(fee_bps=Decimal("1"), slippage_bps=Decimal("5")),
    )
    chosen: Agent = (
        BuyAndHold(symbol, Decimal(quantity))
        if agent is AgentChoice.buy_and_hold
        else SmaCrossover(symbol, Decimal(quantity))
    )
    prices = load_price_data(data, universe)
    simulator = Simulator(prices, config)

    if out is None:
        result = simulator.run(chosen)
    else:
        config_hash = sha256_hex(
            canonical_json({"agent": agent.value, "symbol": symbol, "cash": cash, "qty": quantity})
        )
        header = TraceHeader(
            config_hash=config_hash,
            code_version="0.1.0",
            model_ids=[f"scripted:{agent.value}"],
            submodule_sha=None,
            rng_seed=0,
        )
        with TraceWriter(out, header) as writer:
            result = simulator.run(chosen, writer=writer)
        verification = verify_trace(out)
        typer.echo(f"trace written to {out} ({verification.event_count} events)")
        typer.echo(f"chain hash {verification.final_chain_hash}")

    typer.echo(
        f"RUN OK: proposed={result.proposed} admitted={result.admitted} "
        f"violations={result.violations} final_equity={result.final_equity}"
    )


@app.command()
def report(
    trace: _TracePath,
    out_html: Annotated[Path | None, typer.Option(help="Write the HTML report here")] = None,
    out_json: Annotated[Path | None, typer.Option(help="Write the JSON report here")] = None,
    initial_cash: Annotated[
        str | None, typer.Option(help="Baseline for the equity curve (default: P&L from 0)")
    ] = None,
) -> None:
    """Render an evaluation report (HTML + JSON) from a verified trace."""
    import json

    from tradewind.report import build_report_model, render_report_html

    try:
        model = build_report_model(
            trace, Decimal(initial_cash) if initial_cash is not None else Decimal(0)
        )
    except (TraceIntegrityError, ReplayDivergence) as exc:
        typer.echo(f"FAIL: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if out_html is not None:
        out_html.write_text(render_report_html(model), encoding="utf-8")
        typer.echo(f"HTML report written to {out_html}")
    if out_json is not None:
        out_json.write_text(
            json.dumps(model.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        typer.echo(f"JSON report written to {out_json}")
    verified = "verified" if model.replay_verified else "NOT verified"
    typer.echo(
        f"REPORT OK: {model.event_count} events, {model.proposed} proposed, "
        f"{model.admitted} admitted, {model.violation_count} violations, replay {verified}"
    )


@app.command()
def diff(
    left: _TracePath,
    right: _TracePath,
    out_html: Annotated[Path | None, typer.Option(help="Write the HTML diff here")] = None,
    out_json: Annotated[Path | None, typer.Option(help="Write the JSON diff here")] = None,
) -> None:
    """Diff two traces to find where their decisions first diverged."""
    import json

    from tradewind.report import diff_traces, render_diff_html

    try:
        result = diff_traces(left, right)
    except (TraceIntegrityError, ReplayDivergence) as exc:
        typer.echo(f"FAIL: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if out_html is not None:
        out_html.write_text(render_diff_html(result), encoding="utf-8")
        typer.echo(f"HTML diff written to {out_html}")
    if out_json is not None:
        out_json.write_text(
            json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        typer.echo(f"JSON diff written to {out_json}")
    if result.diverged:
        typer.echo(f"DIVERGED: first at seq {result.first_divergence_seq}")
    else:
        typer.echo("IDENTICAL: traces align on every event")


@app.command()
def bench(
    out: Annotated[
        Path | None,
        typer.Option(help="Directory to write results.md + results.json (default: print only)"),
    ] = None,
) -> None:
    """Run the seeded-fault benchmark and report catch rates.

    Exits 1 if any critical fault (F1/F2/F3/F5) went undetected — an honest
    failure rather than a green light.
    """
    from tradewind.bench import run_benchmark

    report = run_benchmark()
    caught, total = report.catch_rate()
    for fid in report.families():
        f_caught, f_total = report.catch_rate(fid)
        typer.echo(f"{fid}: {f_caught}/{f_total} caught")
    typer.echo(f"ALL: {caught}/{total} caught")

    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.md").write_text(report.to_markdown(), encoding="utf-8")
        import json

        (out / "results.json").write_text(
            json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        typer.echo(f"results written to {out}/results.md and {out}/results.json")

    if not report.critical_all_caught:
        typer.echo("FAIL: a critical fault (F1/F2/F3/F5) went undetected", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
