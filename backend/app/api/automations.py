"""A project's automations (app/automations.py): rules that run its cross-checks on their own
when documents of a kind, or any, arrive in a folder or anywhere in the project, and tell whom
they name what the run found, which the library's Cross-checks view lists beside the rules and
the runs they make.

Every route is behind the flag `extraction_templates` (app/flags.py), as the cross-checks are.
Whoever may change the project makes, changes, turns off and deletes its automations, each change
a row of the organisation's audit log naming the kind, the folder and the cross-checks, never a
value; an automation is written whole and deleted for good, with no trash, the runs it made
staying with the project's. Anyone who sees the project reads them.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    EditableAutomationDep,
    EditableProjectDep,
    ProjectDep,
    SessionDep,
    flagged,
)
from app.audit import audit
from app.automations import MAX_AUTOMATIONS, checks_of, problem_of
from app.extraction import kind_of, templates_for
from app.flags import EXTRACTION_TEMPLATES
from app.folders import folder_paths
from app.models import (
    Automation,
    AutomationCheck,
    CrossCheck,
    Folder,
    Project,
    User,
    WorkflowRun,
)
from app.schemas import AutomationIn, AutomationOut, AutomationsOut

router = APIRouter(tags=["automations"], dependencies=[Depends(flagged(EXTRACTION_TEMPLATES))])

TOO_MANY = (
    f"A project has at most {MAX_AUTOMATIONS} automations. Delete one it no longer needs before "
    "adding another."
)
NO_TEMPLATE = (
    "This project has no template for that kind of document. Choose a kind its templates read, "
    "or any document."
)

# What of an automation a change may change, as the audit log names it, by its columns, and
# `checks` by the cross-checks it chose too.
PARTS = {
    "title": ("title",),
    "watches": ("kind", "folder_id"),
    "checks": ("every_check",),
    "report": ("report",),
    "audience": ("audience",),
    "recheck": ("recheck",),
    "enabled": ("enabled",),
}


@router.get("/projects/{project_id}/automations", response_model=AutomationsOut)
def list_automations(project: ProjectDep, session: SessionDep):
    """The project's automations by name, each with why it would run nothing as the project
    stands now, if it would not, and when it last ran."""
    rows = session.scalars(
        select(Automation)
        .where(Automation.project_id == project.id)
        .order_by(Automation.title, Automation.id)
    ).all()
    return AutomationsOut(
        automations=_outs(session, project.id, list(rows)), max_automations=MAX_AUTOMATIONS
    )


@router.post("/projects/{project_id}/automations", response_model=AutomationOut, status_code=201)
def create_automation(
    project: EditableProjectDep,
    body: AutomationIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Make an automation of the project's cross-checks."""
    columns, chosen = _written(session, project, body)
    # The project's row, so that two made at once cannot pass its ceiling.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    held = session.scalar(select(func.count()).where(Automation.project_id == project.id))
    if held >= MAX_AUTOMATIONS:
        raise HTTPException(409, TOO_MANY)
    row = Automation(
        org_id=project.org_id, project_id=project.id, created_by=account.user.id, **columns
    )
    session.add(row)
    session.flush()
    _choose(session, row, chosen)
    audit(
        session,
        account,
        request,
        "automation_created",
        project_id=project.id,
        target=("automation", row.id),
        detail=_named(session, row, chosen),
    )
    session.commit()
    return _outs(session, project.id, [row])[0]


@router.put("/automations/{automation_id}", response_model=AutomationOut)
def update_automation(
    row: EditableAutomationDep,
    body: AutomationIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the automation anew, whole: what arrives from now on runs it as it says."""
    project = session.get(Project, row.project_id)
    columns, chosen = _written(session, project, body)
    before = _chosen_ids(session, row)
    changed = [
        part
        for part, names in PARTS.items()
        if any(getattr(row, name) != columns[name] for name in names)
    ]
    after = None if chosen is None else sorted(check.id for check in chosen)
    if "checks" not in changed and before != after:
        changed.append("checks")
    if changed:
        for name, value in columns.items():
            setattr(row, name, value)
        _choose(session, row, chosen)
        audit(
            session,
            account,
            request,
            "automation_changed",
            project_id=row.project_id,
            target=("automation", row.id),
            detail={**_named(session, row, chosen), "changed": changed},
        )
    session.commit()
    return _outs(session, row.project_id, [row])[0]


@router.delete("/automations/{automation_id}", status_code=204)
def delete_automation(
    row: EditableAutomationDep, session: SessionDep, account: AccountDep, request: Request
):
    """Delete the automation for good: nothing that arrives runs it any more, and the runs it
    made stay, naming it as it was."""
    audit(
        session,
        account,
        request,
        "automation_deleted",
        project_id=row.project_id,
        target=("automation", row.id),
        detail=_named(session, row, None if row.every_check else checks_of(session, row)),
    )
    session.delete(row)
    session.commit()


def _written(
    session: Session, project: Project, body: AutomationIn
) -> tuple[dict, list[CrossCheck] | None]:
    """The automation's columns as they are kept, and the cross-checks it chose (None for every
    one); refused for a kind the project's templates do not read, a folder that is not the
    project's or is in the trash, and a cross-check that is not the project's."""
    kind = kind_of(body.kind) if body.kind is not None else None
    if kind is not None and kind not in {t.doc_type for t in templates_for(session, project.id)}:
        raise HTTPException(400, NO_TEMPLATE)
    if body.folder_id is not None and not session.scalar(
        select(Folder.id).where(
            Folder.id == body.folder_id,
            Folder.project_id == project.id,
            Folder.deleted_at.is_(None),
        )
    ):
        raise HTTPException(404, "Folder not found")
    chosen = None
    if body.check_ids is not None:
        chosen = list(
            session.scalars(
                select(CrossCheck)
                .where(CrossCheck.project_id == project.id, CrossCheck.id.in_(body.check_ids))
                .order_by(CrossCheck.title, CrossCheck.id)
            )
        )
        if len(chosen) < len(set(body.check_ids)):
            raise HTTPException(404, "Cross-check not found")
    columns = {
        "title": body.title,
        "kind": kind,
        "folder_id": body.folder_id,
        "every_check": chosen is None,
        "report": body.report,
        "audience": body.audience,
        "recheck": body.recheck,
        "enabled": body.enabled,
    }
    return columns, chosen


def _choose(session: Session, row: Automation, chosen: list[CrossCheck] | None) -> None:
    """Keep the cross-checks the automation chose, none when it runs every one."""
    session.execute(delete(AutomationCheck).where(AutomationCheck.automation_id == row.id))
    if chosen:
        session.execute(
            insert(AutomationCheck),
            [{"automation_id": row.id, "cross_check_id": check.id} for check in chosen],
        )


def _chosen_ids(session: Session, row: Automation) -> list[int] | None:
    if row.every_check:
        return None
    return sorted(
        session.scalars(
            select(AutomationCheck.cross_check_id).where(AutomationCheck.automation_id == row.id)
        )
    )


def _named(session: Session, row: Automation, chosen: list[CrossCheck] | None) -> dict:
    """What the audit log keeps of an automation: its name, the kind and the folder it watches,
    the cross-checks it runs by their names, and how it runs them, never a value of a
    document."""
    folder = folder_paths(session, row.project_id, trashed=True).get(row.folder_id)
    return {
        "name": row.title,
        "kind": row.kind,
        "folder": folder,
        "checks": None if chosen is None else [check.title for check in chosen],
        "report": row.report,
        "audience": row.audience,
        "recheck": row.recheck,
        "enabled": row.enabled,
    }


def _outs(session: Session, project_id: int, rows: list[Automation]) -> list[AutomationOut]:
    """The automations as the page shows them, the project's templates, folders, members and
    runs read once for them all."""
    if not rows:
        return []
    templates = templates_for(session, project_id)
    paths = folder_paths(session, project_id, trashed=True)
    folders = {
        folder.id: folder
        for folder in session.scalars(
            select(Folder).where(Folder.id.in_({row.folder_id for row in rows} - {None}))
        )
    }
    makers = dict(
        session.execute(
            select(User.id, User.name).where(User.id.in_({row.created_by for row in rows} - {None}))
        ).all()
    )
    last = dict(
        session.execute(
            select(WorkflowRun.automation_id, func.max(WorkflowRun.created_at))
            .where(WorkflowRun.automation_id.in_([row.id for row in rows]))
            .group_by(WorkflowRun.automation_id)
        ).all()
    )
    outs = []
    for row in rows:
        checks = checks_of(session, row)
        outs.append(
            AutomationOut(
                id=row.id,
                title=row.title,
                kind=row.kind,
                folder_id=row.folder_id,
                check_ids=None if row.every_check else [check.id for check in checks],
                report=row.report,
                audience=row.audience,
                recheck=row.recheck,
                enabled=row.enabled,
                folder=paths.get(row.folder_id) if row.folder_id is not None else None,
                checks=None if row.every_check else [check.title for check in checks],
                created_by=row.created_by,
                made_by=makers.get(row.created_by),
                problem=problem_of(session, row, checks, templates, folders.get(row.folder_id)),
                last_run_at=last.get(row.id),
                updated_at=row.updated_at,
            )
        )
    return outs
