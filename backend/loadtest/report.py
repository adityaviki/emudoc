"""What the load test measured: each measure's percentiles as a member waited them, what the
app did meanwhile, and what went wrong with the run's own work, printed as Markdown."""

import math
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class Row:
    phase: str
    measure: str
    n: int
    p50: float | None
    p95: float | None
    most: float | None
    unit: str = "s"


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # The run's own work that did not get done (a library's document that failed, an answer
    # that did not end done): the figures of such a run are not of the work it set out to do.
    faults: list[str] = field(default_factory=list)

    def add(self, phase: str, measure: str, values: Iterable[float | None], unit: str = "s"):
        kept = sorted(value for value in values if value is not None)
        row = Row(
            phase,
            measure,
            len(kept),
            rank(kept, 0.5),
            rank(kept, 0.95),
            max(kept, default=None),
            unit,
        )
        self.rows.append(row)
        say(f"  {measure}: {_figures(row)}")

    def note(self, line: str) -> None:
        self.notes.append(line)
        say(f"  {line}")

    def fault(self, line: str) -> None:
        self.faults.append(line)
        say(f"  Fault: {line}")

    def markdown(self) -> str:
        lines = [
            "| Phase | Measure | n | p50 | p95 | max |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in self.rows:
            cells = [_number(value, row.unit) for value in (row.p50, row.p95, row.most)]
            lines.append(f"| {row.phase} | {row.measure} | {row.n} | {' | '.join(cells)} |")
        notes = [f"- {note}" for note in self.notes] + [
            f"- Fault: {fault}" for fault in self.faults
        ]
        return "\n".join(lines + [""] + notes)


def rank(values: list[float], share: float) -> float | None:
    """The nearest-rank percentile of sorted `values`: a value a member really waited."""
    if not values:
        return None
    return values[max(0, math.ceil(share * len(values)) - 1)]


def _number(value: float | None, unit: str) -> str:
    if value is None:
        return "–"
    if unit == "s":
        return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"
    return f"{value:,.0f} {unit}"


def _figures(row: Row) -> str:
    p50, p95, most = (_number(value, row.unit) for value in (row.p50, row.p95, row.most))
    return f"n {row.n}, p50 {p50}, p95 {p95}, max {most}"


def say(line: str) -> None:
    print(line, file=sys.stderr, flush=True)
