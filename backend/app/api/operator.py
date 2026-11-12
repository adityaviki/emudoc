"""The operator's area's routes (app/operators.py), each the page's view of what a maintenance
command prints (app/maintenance.py): `usage` for one organisation, `costs` across them, `jobs` and
`failed-jobs`, and the worker's watch (app/watch.py) for the services. They read through the same
functions as the commands, so a page and a command never disagree.

Two of them act, as the commands do: failed documents queued again (`requeue`, whose audit rows name
the operator by id in the organisation's own log), and a model provider switched off or on again
without a restart (app/switches.py), which belongs to no organisation and is logged instead.

Every route sits behind `get_operator`, which answers anyone but an operator as if the route
were not there (404, as for a path no route takes); `everything_else` does the same for a path
or method under /api/operator that no route takes, so that 405 tells nobody a route is there.
The router takes no API key, and a route that changes something asks for the app's header
after the operator is known (`refuse_cross_site`), so that the refusal too says nothing to
anyone else.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.auth import organization_out
from app.api.deps import NOT_FOUND, ServicesDep, SessionDep, refuse_cross_site
from app.api.usage import usage_page
from app.config import get_settings, model_provider
from app.deadletters import NamedJob, named_jobs, requeue
from app.jobs import HISTORY, JOB_STATES, QUEUE_BY_STATE, workers_seen
from app.logs import about
from app.models import (
    JOB_KINDS,
    Job,
    Membership,
    Operator,
    Organization,
    User,
    UserSession,
    WatchAlert,
)
from app.operators import TWO_FACTOR_FIRST, operator_of, two_factor_due
from app.postgres import server
from app.schemas import (
    ConcernOut,
    CostOut,
    DatabaseOut,
    DayCostOut,
    ModelCostOut,
    OperatorCostsOut,
    OperatorJobOut,
    OperatorJobsOut,
    OperatorMemberOut,
    OperatorModelOut,
    OperatorOrganizationOut,
    OperatorOrganizationRow,
    OperatorOrganizationsOut,
    OperatorOut,
    OperatorProviderOut,
    OperatorServicesOut,
    OperatorUserMembershipOut,
    OperatorUserOut,
    OperatorUsersOut,
    OperatorWorkerOut,
    OrganizationCostOut,
    ProviderSwitchIn,
    QueueOut,
    RequeueIn,
    RequeueOut,
)
from app.switches import switch_off, switch_on, switched_off, switches
from app.usage import OrganizationCost, model_costs, organization_costs, summed
from app.version import VERSION
from app.watch import look

# How many days the costs look back unless asked, as the `costs` command does.
COST_DAYS = 30
# The rows of a page of organisations or users, and the dead letters shown at once.
PAGE = 50
FAILED_SHOWN = 200
# A provider as DISABLED_PROVIDERS names one, the prefix of a `provider:model` name.
PROVIDER = r"^[a-z0-9_-]+$"


def get_operator(request: Request, session: SessionDep) -> User:
    """The operator the request is from; 404 for anyone else, and 403 with what to do for an
    operator whose sign-in the area does not take yet."""
    user = operator_of(session, request)
    if user is None:
        raise HTTPException(404, NOT_FOUND)
    about(user_id=user.id)
    if two_factor_due(user):
        raise HTTPException(403, TWO_FACTOR_FIRST)
    return user


OperatorDep = Annotated[User, Depends(get_operator)]


def given[M: BaseModel](model: type[M]) -> Any:
    """A dependency that reads the request's JSON body as `model` once the operator is known.
    FastAPI reads a body parameter before any dependency runs, so a malformed body would be
    answered 422 to anyone, telling them a route is there; read here, it is answered so to an
    operator alone."""

    async def read(request: Request, _: OperatorDep) -> M:
        try:
            return model.model_validate_json(await request.body())
        except ValidationError as error:
            raise RequestValidationError(
                [{**found, "loc": ("body", *found["loc"])} for found in error.errors()]
            ) from None

    return Depends(read)


# The operator first, then the app's header for a change: in that order, so that a request
# refused for its missing header was an operator's.
router = APIRouter(
    prefix="/operator",
    tags=["operator"],
    dependencies=[Depends(get_operator), Depends(refuse_cross_site)],
)


@router.get("/me", response_model=OperatorOut)
def who(operator: OperatorDep):
    """Who opened the area, for its header, and which deployment it is."""
    settings = get_settings()
    return OperatorOut(
        user_id=operator.id,
        name=operator.name,
        email=operator.email,
        environment=settings.environment,
        version=VERSION,
        currency=settings.price_currency,
    )


@router.get("/organizations", response_model=OperatorOrganizationsOut)
def list_organizations(
    session: SessionDep,
    q: str = Query("", max_length=320),
    before: int | None = None,
    limit: int = Query(PAGE, ge=1, le=200),
):
    """The organisations, the newest first, a page at a time, with what each cost over the
    last COST_DAYS days. `q` finds them by id, by a part of their name or slug, or by a
    member's whole address."""
    members = (
        select(func.count())
        .where(Membership.org_id == Organization.id)
        .correlate(Organization)
        .scalar_subquery()
    )
    query: Select = select(Organization, members).order_by(Organization.id.desc())
    if (found := _organizations_matching(q.strip())) is not None:
        query = query.where(found)
    if before is not None:
        query = query.where(Organization.id < before)
    rows = session.execute(query.limit(limit + 1)).all()
    shown = rows[:limit]
    costs = {cost.org_id: cost for cost in organization_costs(session, *_days(COST_DAYS))}
    return OperatorOrganizationsOut(
        organizations=[
            OperatorOrganizationRow(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                members=count,
                created_at=organization.created_at,
                cost_micros=cost.cost_micros if (cost := costs.get(organization.id)) else None,
            )
            for organization, count in shown
        ],
        next_before=shown[-1][0].id if len(rows) > limit else None,
        cost_days=COST_DAYS,
    )


def _organizations_matching(q: str):
    if not q:
        return None
    member = exists().where(
        Membership.org_id == Organization.id,
        Membership.user_id == User.id,
        User.email == q.lower(),
    )
    matches = [
        Organization.name.icontains(q, autoescape=True),
        Organization.slug.icontains(q.lower(), autoescape=True),
        member,
    ]
    if q.isdecimal():
        matches.append(Organization.id == int(q))
    return or_(*matches)


@router.get("/organizations/{org_id}", response_model=OperatorOrganizationOut)
def show_organization(org_id: int, session: SessionDep):
    """One organisation as the `usage` command shows it, with its usage page as its
    members read it, its members and what its work cost over the last COST_DAYS days."""
    organization = session.get(Organization, org_id)
    if organization is None:
        raise HTTPException(404, "No organisation has this id; it may have been deleted.")
    about(org_id=organization.id)
    since, until = _days(COST_DAYS)
    found = [cost for cost in organization_costs(session, since, until) if cost.org_id == org_id]
    cost = found[0] if found else OrganizationCost(org_id, None, 0, 0, 0, 0, 0)
    members = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at, User.id)
    ).all()
    return OperatorOrganizationOut(
        organization=organization_out(organization),
        usage=usage_page(session, organization),
        members=[
            OperatorMemberOut(
                user_id=user.id,
                name=user.name,
                email=user.email,
                role=membership.role,
                mfa_enabled=user.mfa_enabled,
                joined_at=membership.created_at,
            )
            for membership, user in members
        ],
        since=since,
        until=until,
        cost=_cost_out(cost, None),
        cost_days=[
            DayCostOut(day=day, cost_micros=spent) for day, spent in sorted(cost.days.items())
        ],
    )


@router.get("/users", response_model=OperatorUsersOut)
def list_users(session: SessionDep, q: str = Query("", max_length=320)):
    """The users whose address or name holds `q`, the newest first, or the newest of all
    without it, with their organisations and when they were last seen."""
    last_seen = (
        select(func.max(UserSession.last_seen_at))
        .where(UserSession.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    operator = exists().where(Operator.user_id == User.id)
    query: Select = select(User, last_seen, operator).order_by(User.id.desc())
    if q := q.strip():
        query = query.where(
            or_(
                User.email.contains(q.lower(), autoescape=True),
                User.name.icontains(q, autoescape=True),
            )
        )
    rows = session.execute(query.limit(PAGE + 1)).all()
    shown = rows[:PAGE]
    memberships: dict[int, list[OperatorUserMembershipOut]] = {}
    for membership, organization in session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id.in_([user.id for user, _, _ in shown]))
        .order_by(Membership.created_at)
    ):
        memberships.setdefault(membership.user_id, []).append(
            OperatorUserMembershipOut(
                org_id=organization.id,
                slug=organization.slug,
                name=organization.name,
                role=membership.role,
            )
        )
    return OperatorUsersOut(
        users=[
            OperatorUserOut(
                id=user.id,
                name=user.name,
                email=user.email,
                created_at=user.created_at,
                email_verified=user.email_verified_at is not None,
                mfa_enabled=user.mfa_enabled,
                operator=is_operator,
                deletion_due_at=user.deletion_due_at,
                last_seen_at=seen,
                memberships=memberships.get(user.id, []),
            )
            for user, seen, is_operator in shown
        ],
        more=len(rows) > PAGE,
    )


@router.get("/costs", response_model=OperatorCostsOut)
def costs(session: SessionDep, days: int = Query(COST_DAYS, ge=1, le=366)):
    """The `costs` command's report: every organisation that used anything over the last `days`
    days up to today, in UTC, the costliest first; theirs together against DAILY_SPEND_CAP; and the
    cost by kind of work and model."""
    settings = get_settings()
    since, until = _days(days)
    found = organization_costs(session, since, until)
    organizations = {
        organization.id: organization
        for organization in session.scalars(
            select(Organization).where(Organization.id.in_([cost.org_id for cost in found]))
        )
    }
    everyone = session.scalar(select(func.count()).select_from(Organization)) or 0
    return OperatorCostsOut(
        since=since,
        until=until,
        currency=settings.price_currency,
        organizations=[_organization_cost(cost, organizations[cost.org_id]) for cost in found],
        platform=_cost_out(summed(found), settings.daily_spend_cap_micros or None),
        idle=everyone - len(found),
        models=[
            ModelCostOut(
                kind=spent.kind,
                model=spent.model,
                cost_micros=spent.cost_micros,
                calls=spent.calls,
                input_tokens=spent.input_tokens,
                output_tokens=spent.output_tokens,
                unpriced_tokens=spent.unpriced_tokens,
            )
            for spent in model_costs(session, since, until)
        ],
    )


@router.get("/jobs", response_model=OperatorJobsOut)
def jobs(session: SessionDep):
    """The `jobs` and `failed-jobs` commands' report: each kind's jobs by state, the running
    ones with their leases, the dead letters, the newest first, and the workers seen lately."""
    counts: dict[tuple[str, str], int] = {}
    oldest: dict[str, float] = {}
    for kind, state, count, waited in session.execute(QUEUE_BY_STATE):
        counts[kind, state] = count
        if state == "due":
            oldest[kind] = float(waited)
    kinds = [kind for kind in JOB_KINDS if any(found == kind for found, _ in counts)]
    failed = sum(counts.get((kind, "failed"), 0) for kind in kinds)
    newest_failed = (
        select(Job.id)
        .where(Job.status == "failed")
        .order_by(Job.finished_at.desc(), Job.id.desc())
        .limit(FAILED_SHOWN)
    )
    return OperatorJobsOut(
        taken_at=session.scalar(select(func.now())),
        queue=[
            QueueOut(
                kind=kind,
                **{state: counts.get((kind, state), 0) for state in JOB_STATES},
                oldest_due_seconds=oldest.get(kind),
            )
            for kind in kinds
        ],
        running=[_job_out(named) for named in named_jobs(session, Job.status == "running")],
        failed=[
            _job_out(named) for named in reversed(named_jobs(session, Job.id.in_(newest_failed)))
        ],
        failed_total=failed,
        history_days=HISTORY.days,
        workers=[OperatorWorkerOut(**vars(seen)) for seen in workers_seen(session)],
    )


@router.post("/jobs/requeue", response_model=RequeueOut)
def requeue_failed(
    operator: OperatorDep, session: SessionDep, asked: Annotated[RequeueIn, given(RequeueIn)]
):
    """Queue failed documents again from the stage each stopped at, as `failed-jobs --requeue`
    does: the failed jobs `job_ids` names, or every one. Only index jobs run again; the others
    are left as they are. Each document queued is a row of its organisation's audit log naming
    the operator."""
    failed = Job.status == "failed"
    if asked.job_ids is not None:
        failed = and_(failed, Job.id.in_(asked.job_ids))
    # Held first, so that two asking at once take turns: the second finds the jobs the first
    # ran again gone.
    held = session.scalars(select(Job.id).where(failed).order_by(Job.id).with_for_update()).all()
    outcome = requeue(session, named_jobs(session, Job.id.in_(held)), operator=operator.id)
    session.commit()
    return RequeueOut(queued=outcome.queued, lines=outcome.lines)


@router.get("/services", response_model=OperatorServicesOut)
def services_status(services: ServicesDep, session: SessionDep):
    """How the models and services stand: the models the settings name, whether they can be
    used, and everything the worker's watch sees wrong now, asked afresh, each with since when
    the watch has been mailing it."""
    settings = get_settings()
    found, unchecked = look(services, settings)
    raised = dict(session.execute(select(WatchAlert.key, WatchAlert.raised_at)).all())
    report = server(session.connection())
    providers = _providers(session)
    off = {provider.name for provider in providers if provider.off}
    return OperatorServicesOut(
        models=[
            OperatorModelOut(
                variable=model.variable,
                name=model.name,
                role=model.role,
                fallback=model.fallback,
                disabled=model_provider(model.name) in off,
            )
            for model in settings.models
        ],
        providers=providers,
        model_problem=services.llm.problem(),
        embedding_model=settings.embedding_model,
        embedding_address=services.embedder.address,
        scanner=services.scanner is not None,
        storage_origin=services.storage.origin,
        concerns=[
            ConcernOut(
                key=key, title=concern.title, message=concern.message, raised_at=raised.get(key)
            )
            for key, concern in sorted(found.items())
        ],
        unchecked=sorted(unchecked),
        database=DatabaseOut(
            version=report.version,
            role=report.role,
            vector=report.vector,
            iterative_scans=report.iterative_scans,
            timeouts=report.timeouts,
        ),
    )


@router.put("/providers/{provider}", response_model=list[OperatorProviderOut])
def switch_provider(
    operator: OperatorDep,
    session: SessionDep,
    switch: Annotated[ProviderSwitchIn, given(ProviderSwitchIn)],
    provider: str = Path(max_length=40, pattern=PROVIDER),
):
    """Switch a model provider off for every process, or on again, without a restart
    (app/switches.py): off only one the settings name a model of, on again only one the area
    switched off, since DISABLED_PROVIDERS is the deployment's and stays as it is."""
    settings = get_settings()
    if switch.off:
        if provider not in _configured():
            raise HTTPException(
                400, f"No model the settings name is of {provider}, so there is nothing to switch."
            )
        switch_off(session, provider, operator)
    else:
        if provider in settings.disabled:
            raise HTTPException(
                409,
                f"DISABLED_PROVIDERS switches {provider} off: remove it there and restart the "
                "app to switch it on.",
            )
        switch_on(session, provider, operator)
    session.commit()
    # This process at once; the others within SWITCH_SECONDS.
    switched_off.forget()
    return _providers(session)


def _configured() -> dict[str, list[str]]:
    """The providers the settings name a model of, each with the settings that name one."""
    named: dict[str, list[str]] = {}
    for model in get_settings().models:
        if model.name:
            named.setdefault(model_provider(model.name), []).append(model.variable)
    return named


def _providers(session: Session) -> list[OperatorProviderOut]:
    """The providers the settings name a model of, then any other the area holds switched off,
    each with whether it is off and by what."""
    disabled = get_settings().disabled
    held = {switch.provider: (switch, user) for switch, user in switches(session)}
    named = _configured()
    shown = []
    for name in [*named, *sorted(held.keys() - named.keys())]:
        switch, user = held.get(name, (None, None))
        shown.append(
            OperatorProviderOut(
                name=name,
                variables=named.get(name, []),
                off=name in disabled or switch is not None,
                by_setting=name in disabled,
                switched_at=switch.switched_at if switch else None,
                switched_by=(user.name or user.email) if user else None,
            )
        )
    return shown


@router.api_route(
    "/{rest:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def everything_else(rest: str):
    """A path or a method under /api/operator that no route takes: 404 to the operator as to
    anyone else, who `get_operator` has refused already."""
    raise HTTPException(404, NOT_FOUND)


def _days(days: int) -> tuple[date, date]:
    """The span of the last `days` days up to today, in UTC."""
    today = datetime.now(UTC).date()
    return today - timedelta(days=days - 1), today


def _cost_out(cost: OrganizationCost, limit: int | None) -> CostOut:
    return CostOut(
        cost_micros=cost.cost_micros,
        calls=cost.calls,
        input_tokens=cost.input_tokens,
        output_tokens=cost.output_tokens,
        answers=cost.answers,
        pages=cost.pages,
        top_day_micros=cost.top_day,
        limit_micros=limit,
        days_at_limit=cost.days_at(limit) if limit else None,
    )


def _organization_cost(cost: OrganizationCost, organization: Organization) -> OrganizationCostOut:
    return OrganizationCostOut(
        org_id=organization.id,
        slug=organization.slug,
        name=organization.name,
        **_cost_out(cost, None).model_dump(),
    )


def _job_out(named: NamedJob) -> OperatorJobOut:
    job = named.job
    return OperatorJobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        run_at=job.run_at,
        lease_until=job.lease_until,
        finished_at=job.finished_at,
        worker_id=job.worker_id,
        about=named.about,
        last_error=job.last_error,
        reported=job.reported_at is not None,
    )
