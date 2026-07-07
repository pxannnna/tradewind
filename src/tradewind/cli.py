"""Tradewind command-line interface.

Contract: a thin, deterministic shell over the library API. Commands exit
0 on success and 1 on any verification/replay failure, printing the
specific error. Commands for later phases exist as stubs that exit 2 so
scripts fail loudly rather than silently no-op.
"""

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


def _not_yet(phase: str) -> None:
    typer.echo(f"not implemented yet (arrives in {phase})", err=True)
    raise typer.Exit(code=2)


@app.command()
def run() -> None:
    """Run an agent under the harness (Phase 3+)."""
    _not_yet("Phase 3")


@app.command()
def report() -> None:
    """Render an evaluation report from a trace (Phase 5)."""
    _not_yet("Phase 5")


@app.command()
def diff() -> None:
    """Diff two traces to find the first decision divergence (Phase 5)."""
    _not_yet("Phase 5")


@app.command()
def bench() -> None:
    """Run the seeded-fault benchmark (Phase 4)."""
    _not_yet("Phase 4")


if __name__ == "__main__":
    app()
