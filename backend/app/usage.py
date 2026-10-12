"""Usage metering: what each organisation used, recorded as it happens, for its quotas and
its bill.

Every row of `usage_events` is one measure of one thing an organisation did. A row names the
activity (`kind`), what was measured (`unit`) and how much (`quantity`), who did it (the
organisation, the project, the user), the model when one was called, and what it was for
(`ref_type`, `ref_id`: a chat message, a document), so that a figure on a bill can be traced
to its cause. The kinds (USAGE_KINDS in app/models.py) are an answer's model calls (`answer`,
recorded by `answer` in app/answers.py), what an upload brought in (`upload`: its bytes, by
the request in app/api/documents.py, and its pages, by the first parse), the model calls of
the indexing stages (`ocr` with the pages it read, `infer_sections`, `enrich`), the texts
embedded (`embed`) and the pages rendered for the viewer (`render`, app/api/documents.py).

The code that spent something records it, from what it counted, when its work ends however it
ends: a stop, a lost lease or a crash ends an answer's run too, and the calls it made were
made; a stage that failed halfway through its pages made the calls before. So every run is
counted once, and work that a lost worker's successor started over is counted for both runs,
which is what the platform paid for. A `Meter` gathers a run's counts by kind for that
(app/indexing/pipeline.py fills one per document run and records it at the end of every
stage).

`cost_micros` is empty until there is a price table to fill it from. `daily` is the rollup:
an organisation's quantities and costs per day in UTC, by kind and unit, which is what a
quota, a usage page or an invoice reads. It is a query over the events, which is enough until
their number says otherwise; a stored table would then go behind the same function. The
`usage` command (app/maintenance.py) prints it.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from pydantic_ai.usage import RequestUsage, RunUsage
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import UsageEvent

USAGE_COMMAND = "uv run python -m app.maintenance usage"
# The kinds the app records, and what it records them for.
ANSWER = "answer"
UPLOAD = "upload"
OCR = "ocr"
INFER_SECTIONS = "infer_sections"
ENRICH = "enrich"
EMBED = "embed"
RENDER = "render"
CHAT_MESSAGE = "chat_message"
DOCUMENT = "document"

_DAILY = text(
    """
    SELECT (occurred_at AT TIME ZONE 'UTC')::date AS day, kind, unit,
           sum(quantity) AS quantity, sum(cost_micros) AS cost_micros
    FROM usage_events
    WHERE org_id = :org_id AND occurred_at >= :since AND occurred_at < :until
    GROUP BY day, kind, unit
    ORDER BY day, kind, unit
    """
)


@dataclass
class ModelUsage:
    """The model calls one piece of work made, counted as each was answered.

    The agent loop fills one as it goes (`run_agent` in app/agent/loop.py), so whoever runs
    the loop has the calls made so far even when the loop is left early.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage: RequestUsage | RunUsage) -> None:
        """Count one answered request, or every request of one finished run: an indexing
        request is a run that asks again when the first reply failed validation."""
        self.calls += usage.requests if isinstance(usage, RunUsage) else 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    @property
    def measures(self) -> dict[str, int]:
        """The counts by unit, as `record` takes them."""
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class _Spent:
    """What one run spent on one kind: its model calls and whatever else it counted."""

    model: str | None = None
    calls: ModelUsage = field(default_factory=ModelUsage)
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def measures(self) -> dict[str, int]:
        return dict(Counter(self.calls.measures) + self.counts)


class Meter:
    """What one piece of work has used so far, by kind, for the rows to write when it ends.

    The pipeline fills one through a document's run (app/indexing/pipeline.py): a stage counts
    the model calls of a kind into the `ModelUsage` that `calls` hands out, as each is
    answered, and anything else with `add`; the run writes the rows with `record` at the end
    of every stage, however the stage ended, so a stage that failed halfway is charged for the
    calls it made before, and the next stage starts from nothing.
    """

    def __init__(
        self,
        *,
        org_id: int,
        project_id: int | None,
        user_id: int | None,
        ref: tuple[str, int] | None,
    ):
        self._payer = {"org_id": org_id, "project_id": project_id, "user_id": user_id}
        self._ref = ref
        self._spent: dict[str, _Spent] = {}

    def calls(self, kind: str, model: str) -> ModelUsage:
        """The accumulator for the model calls of `kind`, which `model` answers."""
        return self._spent_on(kind, model).calls

    def add(self, kind: str, unit: str, quantity: int, model: str | None = None) -> None:
        """Count `quantity` more of `unit` for `kind`."""
        self._spent_on(kind, model).counts[unit] += quantity

    def _spent_on(self, kind: str, model: str | None) -> _Spent:
        spent = self._spent.setdefault(kind, _Spent())
        if model is not None:
            spent.model = model
        return spent

    def record(self, session: Session) -> list[UsageEvent]:
        """Add one row per kind and unit used since the last call to the caller's transaction,
        and start counting afresh."""
        rows: list[UsageEvent] = []
        for kind, spent in self._spent.items():
            rows += record(
                session,
                **self._payer,
                kind=kind,
                measures=spent.measures,
                model=spent.model,
                ref=self._ref,
            )
        self._spent = {}
        return rows


def record(
    session: Session,
    *,
    org_id: int,
    project_id: int | None,
    user_id: int | None,
    kind: str,
    measures: Mapping[str, int],
    model: str | None = None,
    ref: tuple[str, int] | None = None,
    occurred_at: datetime | None = None,
) -> list[UsageEvent]:
    """Add one row per measure of what was used to the caller's transaction, which also holds
    the work it was for when there is any. A measure of zero adds no row."""
    ref_type, ref_id = ref if ref is not None else (None, None)
    # Left unset, the column's default stamps the row as it is written.
    when = {"occurred_at": occurred_at} if occurred_at is not None else {}
    rows = [
        UsageEvent(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            kind=kind,
            unit=unit,
            quantity=quantity,
            model=model,
            ref_type=ref_type,
            ref_id=ref_id,
            **when,
        )
        for unit, quantity in measures.items()
        if quantity
    ]
    session.add_all(rows)
    return rows


@dataclass(frozen=True)
class DailyUsage:
    """One organisation's use of one unit of one kind on one day (UTC)."""

    day: date
    kind: str
    unit: str
    quantity: int
    # None while no row of the day has a cost.
    cost_micros: int | None


def daily(session: Session, org_id: int, since: date, until: date) -> list[DailyUsage]:
    """The organisation's usage per day in UTC, from `since` to `until` inclusive, by kind
    and unit, in that order."""
    start = datetime.combine(since, time.min, UTC)
    end = datetime.combine(until + timedelta(days=1), time.min, UTC)
    rows = session.execute(_DAILY, {"org_id": org_id, "since": start, "until": end})
    return [DailyUsage(*row) for row in rows]
