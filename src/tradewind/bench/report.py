"""Benchmark result model and rendering (markdown + JSON).

Contract: a :class:`FaultResult` records, per injected fault, what detection was
*expected* and what actually happened when the fault was run against the real
harness. :func:`run_benchmark` collects every scenario into a
:class:`BenchReport`, which computes catch rates and renders both a human table
and a machine-readable object.

Gating: F1, F2, F3, and F5 must reach a 100% catch rate by construction — the
harness is designed so those cannot slip through. F4 (nondeterminism) is
reported honestly but does not gate, because nondeterminism that never
influences a recorded byte is invisible by construction (see the F4 caveat).
"""

from dataclasses import dataclass

CRITICAL_FAMILIES = ("F1", "F2", "F3", "F5")

FAMILY_NAMES = {
    "F1": "Hallucinated instruments",
    "F2": "Accounting attacks",
    "F3": "Limit breaches",
    "F4": "Nondeterminism injection",
    "F5": "Trace tampering",
}


@dataclass(frozen=True)
class FaultResult:
    """The observed outcome of running one injected fault against the harness."""

    fault_id: str
    scenario: str
    description: str
    expected_detection: str
    detected: bool
    mechanism: str
    event_seq: int | None = None

    def to_json(self) -> dict[str, object]:
        """JSON-native form for the machine-readable results file."""
        return {
            "fault_id": self.fault_id,
            "scenario": self.scenario,
            "description": self.description,
            "expected_detection": self.expected_detection,
            "detected": self.detected,
            "mechanism": self.mechanism,
            "event_seq": self.event_seq,
        }


@dataclass(frozen=True)
class BenchReport:
    """All fault results plus catch-rate accounting."""

    results: tuple[FaultResult, ...]

    def catch_rate(self, fault_id: str | None = None) -> tuple[int, int]:
        """Return ``(caught, total)`` overall, or for one fault family."""
        rows = [r for r in self.results if fault_id is None or r.fault_id == fault_id]
        return sum(1 for r in rows if r.detected), len(rows)

    @property
    def critical_all_caught(self) -> bool:
        """Whether every F1/F2/F3/F5 scenario was detected (the gating condition)."""
        return all(r.detected for r in self.results if r.fault_id in CRITICAL_FAMILIES)

    def families(self) -> list[str]:
        """Fault family ids present, in canonical order."""
        order = ["F1", "F2", "F3", "F4", "F5"]
        present = {r.fault_id for r in self.results}
        return [f for f in order if f in present]

    def to_json(self) -> dict[str, object]:
        """Full machine-readable report: per-family summary plus every result."""
        caught, total = self.catch_rate()
        summary: dict[str, object] = {
            "total": total,
            "caught": caught,
            "critical_all_caught": self.critical_all_caught,
            "by_family": {
                fid: {
                    "name": FAMILY_NAMES[fid],
                    "caught": self.catch_rate(fid)[0],
                    "total": self.catch_rate(fid)[1],
                }
                for fid in self.families()
            },
        }
        return {"summary": summary, "results": [r.to_json() for r in self.results]}

    def to_markdown(self) -> str:
        """Render the results table and catch-rate summary as Markdown."""
        lines = [
            "# Tradewind seeded-fault benchmark",
            "",
            "Every row runs an injected fault against the real harness and records "
            "whether it was caught and how. F1–F3 and F5 are caught by construction; "
            "F4 is reported honestly (see the caveat below).",
            "",
            "## Catch rates",
            "",
            "| Family | Fault class | Caught | Total |",
            "| --- | --- | ---: | ---: |",
        ]
        for fid in self.families():
            caught, total = self.catch_rate(fid)
            lines.append(f"| {fid} | {FAMILY_NAMES[fid]} | {caught} | {total} |")
        caught, total = self.catch_rate()
        lines.append(f"| **All** | | **{caught}** | **{total}** |")
        lines += [
            "",
            "## Scenarios",
            "",
            "| Fault | Scenario | Expected detection | Detected? | Mechanism | Seq |",
            "| --- | --- | --- | :---: | --- | ---: |",
        ]
        for r in self.results:
            mark = "✅" if r.detected else "❌"
            seq = "-" if r.event_seq is None else str(r.event_seq)
            lines.append(
                f"| {r.fault_id} | {r.scenario} | {r.expected_detection} | {mark} | "
                f"{r.mechanism} | {seq} |"
            )
        lines += [
            "",
            "## F4 caveat",
            "",
            "The determinism check catches nondeterminism that influences a recorded "
            "boundary request or the event stream — on replay the reissued request "
            "hashes no longer match the recording, so the lookup misses and raises "
            "`ReplayDivergence`. Nondeterminism that never influences any recorded "
            "byte is invisible by construction; the harness cannot flag what it never "
            "observes. The benchmark models the drifting value with a process-lifetime "
            "counter (a deterministic, CI-safe stand-in for a wall-clock read or "
            "unseeded RNG); `tests/test_bench.py` additionally proves the same catch "
            "against genuine `time`/`random` sources.",
            "",
        ]
        return "\n".join(lines)


def run_benchmark() -> BenchReport:
    """Run every fault scenario against the harness and collect the results."""
    from tradewind.bench.scenarios import all_results

    return BenchReport(results=tuple(all_results()))
