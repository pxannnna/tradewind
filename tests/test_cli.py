"""CLI behaviour: verify/replay succeed and fail loudly; future commands stub out."""

from pathlib import Path

from typer.testing import CliRunner

from tradewind.cli import app

runner = CliRunner()


def test_verify_ok(sample_trace: Path) -> None:
    result = runner.invoke(app, ["verify", str(sample_trace)])
    assert result.exit_code == 0
    assert result.stdout.startswith("OK: 5 events")


def test_verify_tampered_fails_with_clear_error(sample_trace: Path, tmp_path: Path) -> None:
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(
        sample_trace.read_bytes().replace(b'"symbol":"AAPL"', b'"symbol":"EVIL"', 1)
    )
    result = runner.invoke(app, ["verify", str(tampered)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "chain-hash mismatch" in result.output


def test_replay_ok_and_writes_out(sample_trace: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    result = runner.invoke(app, ["replay", str(sample_trace), "--out", str(out)])
    assert result.exit_code == 0
    assert "REPLAY OK: 5 events byte-identical" in result.stdout
    assert out.read_bytes() == sample_trace.read_bytes()


def test_replay_without_out_uses_temp(sample_trace: Path) -> None:
    result = runner.invoke(app, ["replay", str(sample_trace)])
    assert result.exit_code == 0
    assert "byte-identical" in result.stdout


def test_replay_tampered_fails(sample_trace: Path, tmp_path: Path) -> None:
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_bytes(
        sample_trace.read_bytes().replace(b'"symbol":"AAPL"', b'"symbol":"EVIL"', 1)
    )
    result = runner.invoke(app, ["replay", str(tampered)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_future_phase_commands_exit_2() -> None:
    for command in ("run", "report", "diff", "bench"):
        result = runner.invoke(app, [command])
        assert result.exit_code == 2, command
        assert "not implemented yet" in result.output
