"""The Administration page's routes, for the workspace's owners and admins: what the maintenance
commands print in a shell (app/maintenance.py), read in a browser by the team that runs the
install. `costs` for the workspace, `jobs` and `failed-jobs`, and the worker's watch
(app/watch.py) for the services. They read through the same functions as the commands, so a
page and a command never disagree.

Until 2026-11-13 this was the operator's area, a page of its own at /operator for the users the
`operators` command named, from the networks OPERATOR_NETWORKS listed (S.3). An install is a
team's own server now, run by its admins, so the routes sit beside the workspace's own
(app/api/organizations.py) and are held by role, as its API keys and webhooks are. Two-factor
sign-in for the admins is the workspace's choice (`organizations.mfa_required`, app/security.py).

What names an organisation is the workspace's own: its jobs, with those of no organisation such
as mail about an account, and its costs. What belongs to the install, the workers, the models,
the services and the database, is shown whole: an install holds one workspace, and a database
that holds several (development's) is its developer's.

Two of them act, as the commands do: failed documents queued again (`requeue`, whose audit rows
are the admin's), and a model provider switched off or on again for every process without a
restart (app/switches.py), a row of the workspace's audit log too.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.deps import AccountDep, ServicesDep, SessionDep
from app.audit import audit
from app.config import get_settings, model_provider
from app.deadletters import NamedJob, named_jobs, requeue
from app.jobs import HISTORY, JOB_STATES, QUEUE_BY_STATE, workers_seen
from app.models import JOB_KINDS, Job, WatchAlert
from app.postgres import server
from app.schemas import (
    AdminCostsOut,
    AdminJobOut,
    AdminJobsOut,
    AdminModelOut,
    AdminProviderOut,
    AdminServicesOut,
    AdminWorkerOut,
    ConcernOut,
    CostOut,
    DatabaseOut,
    ModelCostOut,
    ProviderSwitchIn,
    QueueOut,
    RequeueIn,
    RequeueOut,
)
from app.switches import switch_off, switch_on, switched_off, switches
from app.usage import OrganizationCost, model_costs, organization_costs
from app.watch import look

ADMINS_ONLY = (
    "Only the owners and admins of the workspace can open its administration: the jobs, the "
    "services, the model providers and what the models cost. Ask one of them, if something "
    "there needs doing."
)
# How many days the costs look back unless asked, as the `costs` command does.
COST_DAYS = 30
# The dead letters shown at once.
FAILED_SHOWN = 200
# A provider as DISABLED_PROVIDERS names one, the prefix of a `provider:model` name.
PROVIDER = r"^[a-z0-9_-]+$"


def get_admin(account: AccountDep) -> Account:
    """The account of an owner or admin of the workspace; 403 with why for anyone else."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)
    return account


AdminDep = Annotated[Account, Depends(get_admin)]

router = APIRouter(prefix="/organization", tags=["admin"], dependencies=[Depends(get_admin)])


def _theirs(account: Account):
    """The jobs the workspace's admins are shown: its own, and those of no organisation."""
    return or_(Job.org_id == account.organization.id, Job.org_id.is_(None))


@router.get("/costs", response_model=AdminCostsOut)
def costs(admin: AdminDep, session: SessionDep, days: int = Query(COST_DAYS, ge=1, le=366)):
    """The `costs` command's report for the workspace: what its work cost over the last `days`
    days up to today, in UTC, against DAILY_SPEND_CAP, and by kind of work and model."""
    settings = get_settings()
    org_id = admin.organization.id
    since, until = _days(days)
    found = organization_costs(session, since, until, org_id=org_id)
    cost = found[0] if found else OrganizationCost(org_id, None, 0, 0, 0, 0, 0)
    limit = settings.daily_spend_cap_micros or None
    return AdminCostsOut(
        since=since,
        until=until,
        currency=settings.price_currency,
        cost=CostOut(
            cost_micros=cost.cost_micros,
            calls=cost.calls,
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
            answers=cost.answers,
            pages=cost.pages,
            top_day_micros=cost.top_day,
            limit_micros=limit,
            days_at_limit=cost.days_at(limit) if limit else None,
        ),
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
            for spent in model_costs(session, since, until, org_id=org_id)
        ],
    )


@router.get("/jobs", response_model=AdminJobsOut)
def jobs(admin: AdminDep, session: SessionDep):
    """The `jobs` and `failed-jobs` commands' report for the workspace: each kind's jobs by
    state, the running ones with their leases, the dead letters, the newest first, and the
    workers seen lately."""
    counts: dict[tuple[str, str], int] = {}
    oldest: dict[str, float] = {}
    queue = session.execute(QUEUE_BY_STATE, {"org_id": admin.organization.id})
    for kind, state, count, waited in queue:
        counts[kind, state] = count
        if state == "due":
            oldest[kind] = float(waited)
    kinds = [kind for kind in JOB_KINDS if any(found == kind for found, _ in counts)]
    failed = sum(counts.get((kind, "failed"), 0) for kind in kinds)
    theirs = _theirs(admin)
    newest_failed = (
        select(Job.id)
        .where(Job.status == "failed", theirs)
        .order_by(Job.finished_at.desc(), Job.id.desc())
        .limit(FAILED_SHOWN)
    )
    return AdminJobsOut(
        taken_at=session.scalar(select(func.now())),
        queue=[
            QueueOut(
                kind=kind,
                **{state: counts.get((kind, state), 0) for state in JOB_STATES},
                oldest_due_seconds=oldest.get(kind),
            )
            for kind in kinds
        ],
        running=[
            _job_out(named) for named in named_jobs(session, and_(Job.status == "running", theirs))
        ],
        failed=[
            _job_out(named) for named in reversed(named_jobs(session, Job.id.in_(newest_failed)))
        ],
        failed_total=failed,
        history_days=HISTORY.days,
        workers=[AdminWorkerOut(**vars(seen)) for seen in workers_seen(session)],
    )


@router.post("/jobs/requeue", response_model=RequeueOut)
def requeue_failed(asked: RequeueIn, admin: AdminDep, session: SessionDep):
    """Queue the workspace's failed documents again from the stage each stopped at, as
    `failed-jobs --requeue` does: the failed jobs `job_ids` names, or every one. Only index jobs
    run again; the others are left as they are. Each document queued is a row of the audit log,
    the admin's."""
    failed = and_(Job.status == "failed", _theirs(admin))
    if asked.job_ids is not None:
        failed = and_(failed, Job.id.in_(asked.job_ids))
    # Held first, so that two asking at once take turns: the second finds the jobs the first
    # ran again gone.
    held = session.scalars(select(Job.id).where(failed).order_by(Job.id).with_for_update()).all()
    outcome = requeue(session, named_jobs(session, Job.id.in_(held)), by=admin.user.id)
    session.commit()
    return RequeueOut(queued=outcome.queued, lines=outcome.lines)


@router.get("/services", response_model=AdminServicesOut)
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
    return AdminServicesOut(
        models=[
            AdminModelOut(
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


@router.put("/providers/{provider}", response_model=list[AdminProviderOut])
def switch_provider(
    switch: ProviderSwitchIn,
    admin: AdminDep,
    request: Request,
    session: SessionDep,
    provider: str = Path(max_length=40, pattern=PROVIDER),
):
    """Switch a model provider off for every process, or on again, without a restart
    (app/switches.py): off only one the settings name a model of, on again only one the page
    switched off, since DISABLED_PROVIDERS is the install's own and stays as it is. A switch
    that changed something is a row of the audit log."""
    settings = get_settings()
    if switch.off:
        if provider not in _configured():
            raise HTTPException(
                400, f"No model the settings name is of {provider}, so there is nothing to switch."
            )
        switched = switch_off(session, provider, admin.user)
    else:
        if provider in settings.disabled:
            raise HTTPException(
                409,
                f"DISABLED_PROVIDERS switches {provider} off: remove it there and restart the "
                "app to switch it on.",
            )
        switched = switch_on(session, provider, admin.user)
    if switched:
        audit(
            session,
            admin,
            request,
            "provider_switched",
            detail={"provider": provider, "off": switch.off},
        )
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


def _providers(session: Session) -> list[AdminProviderOut]:
    """The providers the settings name a model of, then any other the page holds switched off,
    each with whether it is off and by what."""
    disabled = get_settings().disabled
    held = {switch.provider: (switch, user) for switch, user in switches(session)}
    named = _configured()
    shown = []
    for name in [*named, *sorted(held.keys() - named.keys())]:
        switch, user = held.get(name, (None, None))
        shown.append(
            AdminProviderOut(
                name=name,
                variables=named.get(name, []),
                off=name in disabled or switch is not None,
                by_setting=name in disabled,
                switched_at=switch.switched_at if switch else None,
                switched_by=(user.name or user.email) if user else None,
            )
        )
    return shown


def _days(days: int) -> tuple[date, date]:
    """The span of the last `days` days up to today, in UTC."""
    today = datetime.now(UTC).date()
    return today - timedelta(days=days - 1), today


def _job_out(named: NamedJob) -> AdminJobOut:
    job = named.job
    return AdminJobOut(
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
