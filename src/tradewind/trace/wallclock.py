"""The ONLY module in Tradewind allowed to touch the wall clock.

Contract: real time is an unrecordable side effect, so core code must never
read it. The single sanctioned use is measuring the latency of a *live*
provider call while recording — a value that is stored in the trace and
replayed verbatim, so it never re-enters any deterministic computation.

The no-wallclock lint test (``tests/test_no_wallclock.py``) greps all of
``src/tradewind`` for wall-clock and global-``random`` usage and exempts
exactly this file. Do not add imports of :mod:`time` or :mod:`datetime`
anywhere else in the core.
"""

import time


class WallTimer:
    """Measures elapsed real time in milliseconds (record mode only)."""

    def start(self) -> float:
        """Return an opaque start marker."""
        return time.perf_counter()

    def elapsed_ms(self, start_marker: float) -> int:
        """Return whole milliseconds elapsed since ``start_marker``."""
        return int((time.perf_counter() - start_marker) * 1000)
