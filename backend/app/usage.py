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
embedded (`embed`, with the tokens the endpoint says they took) and the pages rendered for the
viewer (`render`: the member's by app/api/documents.py, the organisation's for a PDF's first
pages drawn ahead by app/rendering.py).

The code that spent something records it, from what it counted, when its work ends however it
ends: a stop, a lost lease or a crash ends an answer's run too, and the calls it made were
made; a stage that failed halfway through its pages made the calls before. So every run is
counted once, and work that a lost worker's successor started over is counted for both runs,
which is what the platform paid for. A `Meter` gathers a run's counts by kind for that
(app/indexing/pipeline.py fills one per document run and records it at the end of every
stage).

A row's cost (`cost_micros`, millionths of PRICE_CURRENCY) comes from the prices the
settings list per model (MODEL_PRICES, app/config.py), for the tokens a model took in and
gave out, an embedding model's included; a row for a model without a price, or for a unit that
is not tokens, has none.

The message or document a row was for shows what it used: `ChatMessage.usage` and
`Document.usage` (app/models.py) hold the rows for the row, by kind, each unit summed over
every run, the model the latest row named, and the cost summed when any row has one. `record`
adds to it as it adds the rows, in the same transaction, so the two never drift apart.

`daily` is the rollup: an organisation's quantities and costs per day in UTC, by kind and
unit, which is what a quota, a usage page or an invoice reads. It is a query over the events,
which is enough until their number says otherwise; a stored table would then go behind the
same function. The `usage` command (app/maintenance.py) prints it.

`organization_costs` and `model_costs` are the platform's side of the same rows: what each
organisation's work cost over a span of days, costliest first, with the days that make it up,
and what each kind of work cost by the model that did it. The `costs` command prints them for
the operator; an internal admin page would read the same functions.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from pydantic_ai.usage import RequestUsage, RunUsage
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ChatMessage, Document, UsageEvent

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
# The row a reference names, which carries the usage recorded for it.
_REFERENCED = {CHAT_MESSAGE: ChatMessage, DOCUMENT: Document}

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
# Postgres sums a bigint as a numeric, which Python reads as a Decimal; the casts keep the
# figures the ints they count. An answer's rows name its message, so the answers are the
# messages counted once each, however many runs, days and models they took; the pages are those
# the first parse of each upload counted.
_BY_ORGANIZATION = text(
    """
    SELECT org_id, sum(cost_micros)::bigint AS cost_micros,
           coalesce(sum(quantity) FILTER (WHERE unit = 'calls'), 0)::bigint AS calls,
           coalesce(sum(quantity) FILTER (WHERE unit = 'input_tokens'), 0)::bigint
               AS input_tokens,
           coalesce(sum(quantity) FILTER (WHERE unit = 'output_tokens'), 0)::bigint
               AS output_tokens,
           count(DISTINCT ref_id) FILTER (WHERE kind = 'answer') AS answers,
           coalesce(sum(quantity) FILTER (WHERE kind = 'upload' AND unit = 'pages'), 0)::bigint
               AS pages
    FROM usage_events
    WHERE occurred_at >= :since AND occurred_at < :until
    GROUP BY org_id
    """
)
_DAILY_COSTS = text(
    """
    SELECT org_id, (occurred_at AT TIME ZONE 'UTC')::date AS day, sum(cost_micros)::bigint
    FROM usage_events
    WHERE occurred_at >= :since AND occurred_at < :until AND cost_micros IS NOT NULL
    GROUP BY org_id, day
    """
)
_BY_MODEL = text(
    """
    SELECT kind, model, sum(cost_micros)::bigint AS cost_micros,
           coalesce(sum(quantity) FILTER (WHERE unit = 'calls'), 0)::bigint AS calls,
           coalesce(sum(quantity) FILTER (WHERE unit = 'input_tokens'), 0)::bigint
               AS input_tokens,
           coalesce(sum(quantity) FILTER (WHERE unit = 'output_tokens'), 0)::bigint
               AS output_tokens,
           coalesce(
               sum(quantity) FILTER (
                   WHERE unit IN ('input_tokens', 'output_tokens') AND cost_micros IS NULL
               ),
               0
           )::bigint AS unpriced_tokens
    FROM usage_events
    WHERE occurred_at >= :since AND occurred_at < :until AND model IS NOT NULL
    GROUP BY kind, model
    ORDER BY sum(cost_micros) DESC NULLS LAST, calls DESC, kind, model
    """
)


@dataclass
class Calls:
    """The requests one model answered for one piece of work, and the tokens they took."""

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
class ModelUsage:
    """The model calls one piece of work made, by the model that answered each, counted as
    each was answered.

    By model, since a fallback (app/llm.py) answers the requests its primary could not, at a
    price of its own, and one run may use both. A model is named as the settings name it
    (`provider:model`), which is how MODEL_PRICES prices it, never as a reply names it. The
    agent loop fills one as it goes (`run_agent` in app/agent/loop.py), so whoever runs the
    loop has the calls made so far even when the loop is left early.
    """

    models: dict[str, Calls] = field(default_factory=dict)

    def add(self, model: str, usage: RequestUsage | RunUsage) -> None:
        """Count what `model` answered (`Calls.add`). A run of no requests counts tokens
        alone, as an embeddings endpoint reports them for texts that are no model call."""
        self.models.setdefault(model, Calls()).add(usage)

    def include(self, other: "ModelUsage") -> None:
        """Count the calls `other` counted, model by model: a scan's pages are read on threads
        of their own, each into a `ModelUsage` of its own, since the counts are not safe to
        add to from several threads at once (app/indexing/pipeline.py)."""
        for model, spent in other.models.items():
            mine = self.models.setdefault(model, Calls())
            mine.calls += spent.calls
            mine.input_tokens += spent.input_tokens
            mine.output_tokens += spent.output_tokens

    @property
    def calls(self) -> int:
        return sum(spent.calls for spent in self.models.values())

    @property
    def model(self) -> str | None:
        """The model that began answering last, which the row the calls were for names once
        they are recorded (`add` below); None while nothing was answered."""
        return next(reversed(self.models), None)

    @property
    def measures(self) -> dict[str, int]:
        """The counts by unit, summed over the models."""
        return {
            unit: sum(spent.measures[unit] for spent in self.models.values())
            for unit in Calls().measures
        }

    def spent(self) -> dict[str, Any]:
        """The calls as the row they were for shows them once recorded: the counts summed,
        the model that began answering last, and each model's calls priced at its own price,
        added up."""
        costs = [
            cost_of(model, unit, quantity)
            for model, spent in self.models.items()
            for unit, quantity in spent.measures.items()
        ]
        return {"model": self.model, **self.measures, "cost_micros": _total(costs)}


@dataclass
class _Spent:
    """What one run spent on one kind: its model calls, by model, and whatever else it
    counted, with the model those counts were given with."""

    calls: ModelUsage = field(default_factory=ModelUsage)
    counts: Counter[str] = field(default_factory=Counter)
    model: str | None = None


class Meter:
    """What one piece of work has used so far, by kind, for the rows to write when it ends.

    The pipeline fills one through a document's run (app/indexing/pipeline.py): a stage hands
    the `ModelUsage` that `calls` gives out to the model, which counts each request into it
    under the model that answered, and counts anything else with `add`; the run writes the
    rows with `record` at the end of every stage, however the stage ended, so a stage that
    failed halfway is charged for the calls it made before, and the next stage starts from
    nothing.
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

    def calls(self, kind: str) -> ModelUsage:
        """The accumulator for the model calls of `kind`."""
        return self._spent.setdefault(kind, _Spent()).calls

    def add(self, kind: str, unit: str, quantity: int, model: str | None = None) -> None:
        """Count `quantity` more of `unit` for `kind`."""
        spent = self._spent.setdefault(kind, _Spent())
        spent.counts[unit] += quantity
        if model is not None:
            spent.model = model

    def record(self, session: Session) -> list[UsageEvent]:
        """Add the rows of what was used since the last call to the caller's transaction, and
        start counting afresh: one per kind, model and unit for the calls, each model's at its
        own price, and one per kind and unit for the other counts, which name the model they
        were given with or else the one that began answering the kind's calls last (the pages
        a scan's model read)."""
        rows: list[UsageEvent] = []
        for kind, spent in self._spent.items():
            for model, calls in spent.calls.models.items():
                rows += record(
                    session,
                    **self._payer,
                    kind=kind,
                    measures=calls.measures,
                    model=model,
                    ref=self._ref,
                )
            rows += record(
                session,
                **self._payer,
                kind=kind,
                measures=spent.counts,
                model=spent.model or spent.calls.model,
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
    the work it was for when there is any, and add them to the usage the referenced row shows.
    A measure of zero adds no row."""
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
            cost_micros=cost_of(model, unit, quantity),
            ref_type=ref_type,
            ref_id=ref_id,
            **when,
        )
        for unit, quantity in measures.items()
        if quantity
    ]
    session.add_all(rows)
    if ref is not None and rows:
        _keep(session, ref, rows)
    return rows


def cost_of(model: str | None, unit: str, quantity: int) -> int | None:
    """What `quantity` of `unit` from `model` cost, in millionths of PRICE_CURRENCY, by the
    model's price; None without a price for the model, or for a unit that is not tokens."""
    if model is None or (price := get_settings().prices.get(model)) is None:
        return None
    per_million = {"input_tokens": price.input, "output_tokens": price.output}.get(unit)
    if per_million is None:
        return None
    # A price per million tokens is the cost of one token in millionths, so no scaling.
    return int(round(Decimal(quantity) * per_million))


def money(micros: int) -> str:
    """An amount in millionths of PRICE_CURRENCY as the user reads it: USD 1.25."""
    return f"{get_settings().price_currency} {micros / 1_000_000:,.2f}"


def _total(costs: Iterable[int | None]) -> int | None:
    """The costs that are known added up; None while none is."""
    known = [cost for cost in costs if cost is not None]
    return sum(known) if known else None


def add(usage: Mapping[str, Mapping[str, Any]], rows: Iterable[UsageEvent]) -> dict[str, dict]:
    """`usage` with the rows added, as a row's `usage` column holds it: by kind, each unit
    summed, the model the latest row named, the cost summed when any row has one."""
    merged = {kind: dict(spent) for kind, spent in usage.items()}
    for row in rows:
        spent = merged.setdefault(row.kind, {})
        spent[row.unit] = spent.get(row.unit, 0) + row.quantity
        if row.model is not None:
            spent["model"] = row.model
        if row.cost_micros is not None:
            spent["cost_micros"] = spent.get("cost_micros", 0) + row.cost_micros
    return merged


def _keep(session: Session, ref: tuple[str, int], rows: Iterable[UsageEvent]) -> None:
    """Add the rows to the usage the message or document they were for shows."""
    ref_type, ref_id = ref
    # Read again under the row's lock: the object the session holds may be behind the row (a
    # page rendered while the stage ran, the run that was lost charging beside this one), and
    # two runs adding at once must each see the other's sum. A row that is gone, deleted while
    # the run went on, has nothing to show; the events stand on their own.
    referenced = session.get(
        _REFERENCED[ref_type], ref_id, with_for_update=True, populate_existing=True
    )
    if referenced is not None:
        referenced.usage = add(referenced.usage, rows)


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
    rows = session.execute(_DAILY, {"org_id": org_id, **_span(since, until)})
    return [DailyUsage(*row) for row in rows]


@dataclass(frozen=True)
class OrganizationCost:
    """What one organisation's work came to over a span of days in UTC, or every
    organisation's together (`summed`)."""

    # None for every organisation's together.
    org_id: int | None
    # None while no row of the span has a cost.
    cost_micros: int | None
    calls: int
    input_tokens: int
    output_tokens: int
    # The answers that called a model, and the pages the uploads brought in.
    answers: int
    pages: int
    # The known cost of each day of the span that has one.
    days: dict[date, int] = field(default_factory=dict)

    @property
    def top_day(self) -> int | None:
        """The known cost of the costliest day; None while no day has one."""
        return max(self.days.values(), default=None)

    def days_at(self, cap: int) -> int:
        """How many days spent `cap` or more: the days a daily limit of `cap` was reached."""
        return sum(1 for spent in self.days.values() if spent >= cap)


@dataclass(frozen=True)
class ModelCost:
    """What one kind of work cost by one model over a span of days in UTC."""

    kind: str
    model: str
    # None while no row has a cost.
    cost_micros: int | None
    calls: int
    input_tokens: int
    output_tokens: int
    # The tokens recorded without a price, which no cost counts.
    unpriced_tokens: int


def organization_costs(session: Session, since: date, until: date) -> list[OrganizationCost]:
    """Every organisation that used anything from `since` to `until` inclusive, in UTC, with
    what it came to: the costliest first, then the busiest, so that the organisations whose
    models have no price still come in order of their calls."""
    span = _span(since, until)
    days: dict[int, dict[date, int]] = {}
    for org_id, day, cost in session.execute(_DAILY_COSTS, span):
        days.setdefault(org_id, {})[day] = cost
    costs = [
        OrganizationCost(*row, days=days.get(row.org_id, {}))
        for row in session.execute(_BY_ORGANIZATION, span)
    ]
    unknown = -1
    return sorted(
        costs,
        key=lambda cost: (
            -(unknown if cost.cost_micros is None else cost.cost_micros),
            -cost.calls,
            cost.org_id,
        ),
    )


def summed(costs: Iterable[OrganizationCost]) -> OrganizationCost:
    """The organisations' figures added up, day by day: the platform's."""
    costs = list(costs)
    days: Counter[date] = Counter()
    for cost in costs:
        days.update(cost.days)
    return OrganizationCost(
        org_id=None,
        cost_micros=_total(cost.cost_micros for cost in costs),
        calls=sum(cost.calls for cost in costs),
        input_tokens=sum(cost.input_tokens for cost in costs),
        output_tokens=sum(cost.output_tokens for cost in costs),
        answers=sum(cost.answers for cost in costs),
        pages=sum(cost.pages for cost in costs),
        days=dict(days),
    )


def model_costs(session: Session, since: date, until: date) -> list[ModelCost]:
    """What each kind of work cost by each model that did it, from `since` to `until`
    inclusive, in UTC, the costliest first."""
    return [ModelCost(*row) for row in session.execute(_BY_MODEL, _span(since, until))]


def _span(since: date, until: date) -> dict[str, datetime]:
    """The query parameters for the days from `since` to `until` inclusive, in UTC."""
    return {
        "since": datetime.combine(since, time.min, UTC),
        "until": datetime.combine(until + timedelta(days=1), time.min, UTC),
    }
