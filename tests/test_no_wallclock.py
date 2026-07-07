"""Lint-by-test: wall clock and global random are forbidden in core code.

The only sanctioned exception is ``src/tradewind/trace/wallclock.py`` (see
its docstring). Everything else must take time from VirtualClock and
randomness from SeededRand.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "tradewind"

EXEMPT = {SRC / "trace" / "wallclock.py"}

FORBIDDEN = [
    re.compile(r"\btime\.(time|monotonic|perf_counter|process_time|sleep)\s*\("),
    re.compile(r"\bdatetime\.(now|utcnow|today)\s*\("),
    re.compile(r"\bdate\.today\s*\("),
    re.compile(r"^\s*import\s+time\b", re.MULTILINE),
    # Global random module: only the seeded `random.Random` class may be used.
    re.compile(
        r"\brandom\.(random|randint|randrange|choice|choices|shuffle|uniform|gauss|seed)\s*\("
    ),
    re.compile(r"^\s*import\s+random\b", re.MULTILINE),
]


def test_core_code_never_touches_wallclock_or_global_random() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(SRC.parent.parent)}: forbidden pattern {pattern.pattern!r}"
            for pattern in FORBIDDEN
            if pattern.search(text)
        )
    assert not offenders, "\n".join(offenders)


def test_exempt_module_exists_and_is_small() -> None:
    """The wallclock escape hatch must stay tiny and documented."""
    wallclock = SRC / "trace" / "wallclock.py"
    assert wallclock.exists()
    text = wallclock.read_text(encoding="utf-8")
    assert "ONLY module" in text
    assert len(text.splitlines()) < 60
