"""Shared fixtures."""

from pathlib import Path

import pytest

from tests.helpers import record_sample_trace


@pytest.fixture()
def sample_trace(tmp_path: Path) -> Path:
    trace_path = tmp_path / "sample.jsonl"
    record_sample_trace(trace_path)
    return trace_path
