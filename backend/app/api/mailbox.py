"""A project's address (app/inbound.py): whose mail becomes its documents, from whom it takes it,
and what became of the messages it received lately, which the library's "Email in" shows.

Every route is behind the flag `email_in` (app/flags.py). Whoever may change the project gives
it an address, changes whom it takes mail from and where what arrives goes, gives it a new
address in place of the old one, which then reaches nothing, and takes it away; each a row of
the organisation's audit log that counts the senders and never names them. Anyone who sees the
project reads its address and its messages.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    EditableProjectDep,
    ProjectDep,
    SessionDep,
    flagged,
)
from app.audit import audit
from app.config import get_settings
from app.flags import EMAIL_IN
from app.folders import folder_paths
from app.inbound import (
    INBOUND_DAYS,
    MAX_SENDERS,
    NO_DOMAIN,
    NO_DOMAIN_MEMBERS,
    NO_MAILBOX,
    SAMPLE_MAILBOX,
    SHOWN,
    TOO_MANY_SENDERS,
    address_of,
    new_token,
    sender_entry,
)
from app.models import Folder, InboundMessage, Project, ProjectMailbox, User
from app.schemas import InboundMessageOut, InboundRejectedOut, MailboxIn, MailboxOut

router = APIRouter(tags=["mailbox"], dependencies=[Depends(flagged(EMAIL_IN))])


@router.get("/projects/{project_id}/mailbox", response_model=MailboxOut)
def get_mailbox(project: ProjectDep, session: SessionDep):
    """The project's address, or that it has none, with the messages it received lately."""
    return _out(session, project, _mailbox(session, project.id))


@router.put("/projects/{project_id}/mailbox", response_model=MailboxOut)
def set_mailbox(
    project: EditableProjectDep,
    body: MailboxIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Give the project an address taking mail from these senders into this folder, or change
    whom the one it has takes mail from and where it goes."""
    domain = get_settings().inbound_domain
    if not domain:
        raise HTTPException(409, shown_no_domain())
    if project.sample:
        raise HTTPException(409, SAMPLE_MAILBOX)
    senders = _senders(body.senders)
    if body.folder_id is not None:
        folder = session.get(Folder, body.folder_id)
        if folder is None or folder.project_id != project.id or folder.deleted_at is not None:
            raise HTTPException(404, "Folder not found")
    mailbox = _mailbox(session, project.id, lock=True)
    paths = folder_paths(session, project.id)
    if mailbox is None:
        mailbox = ProjectMailbox(
            org_id=project.org_id,
            project_id=project.id,
            token=new_token(),
            senders=senders,
            folder_id=body.folder_id,
            created_by=account.user.id,
        )
        session.add(mailbox)
        session.flush()
        audit(
            session,
            account,
            request,
            "mailbox_created",
            project_id=project.id,
            target=("project", project.id),
            detail={"senders": len(senders), "folder": paths.get(body.folder_id or -1)},
        )
    else:
        changed = []
        if senders != mailbox.senders:
            changed.append("senders")
        if body.folder_id != mailbox.folder_id:
            changed.append("folder")
        if changed:
            before = set(mailbox.senders)
            audit(
                session,
                account,
                request,
                "mailbox_changed",
                project_id=project.id,
                target=("project", project.id),
                detail={
                    "changed": changed,
                    "senders": len(senders),
                    "added": len(set(senders) - before),
                    "removed": len(before - set(senders)),
                    "folder": paths.get(body.folder_id or -1),
                },
            )
            mailbox.senders = senders
            mailbox.folder_id = body.folder_id
    session.commit()
    return _out(session, project, mailbox)


@router.post("/projects/{project_id}/mailbox/address", response_model=MailboxOut)
def new_address(
    project: EditableProjectDep, session: SessionDep, account: AccountDep, request: Request
):
    """Give the project a new address in place of the one it has, which then reaches nothing:
    for an address that was given out too widely."""
    mailbox = _mailbox(session, project.id, lock=True)
    if mailbox is None:
        raise HTTPException(404, NO_MAILBOX)
    mailbox.token = new_token()
    audit(
        session,
        account,
        request,
        "mailbox_changed",
        project_id=project.id,
        target=("project", project.id),
        detail={"changed": ["address"], "senders": len(mailbox.senders)},
    )
    session.commit()
    return _out(session, project, mailbox)


@router.delete("/projects/{project_id}/mailbox", status_code=204)
def delete_mailbox(
    project: EditableProjectDep, session: SessionDep, account: AccountDep, request: Request
):
    """Take the project's address away: mail to it reaches nothing, and what became of the
    messages it received stays until it is forgotten as ever."""
    mailbox = _mailbox(session, project.id, lock=True)
    if mailbox is None:
        raise HTTPException(404, NO_MAILBOX)
    session.delete(mailbox)
    audit(
        session,
        account,
        request,
        "mailbox_deleted",
        project_id=project.id,
        target=("project", project.id),
        detail={"senders": len(mailbox.senders)},
    )
    session.commit()


def shown_no_domain() -> str:
    """Why no project can have an address, in the words of whoever reads it."""
    return NO_DOMAIN if get_settings().environment == "development" else NO_DOMAIN_MEMBERS


def _mailbox(session: Session, project_id: int, lock: bool = False) -> ProjectMailbox | None:
    statement = select(ProjectMailbox).where(ProjectMailbox.project_id == project_id)
    return session.scalar(statement.with_for_update() if lock else statement)


def _senders(entries: list[str]) -> list[str]:
    """The list as it is kept: each entry lower-cased, an address or a bare domain, once, in the
    order given; 400 with the first that cannot be one."""
    senders: list[str] = []
    for entry in entries:
        if not entry.strip():
            continue
        value, problem = sender_entry(entry)
        if problem:
            raise HTTPException(400, problem)
        if value not in senders:
            senders.append(value)
    if len(senders) > MAX_SENDERS:
        raise HTTPException(400, TOO_MANY_SENDERS)
    return senders


def _out(session: Session, project: Project, mailbox: ProjectMailbox | None) -> MailboxOut:
    domain = get_settings().inbound_domain
    folder = None
    if mailbox is not None and mailbox.folder_id is not None:
        folder = folder_paths(session, project.id).get(mailbox.folder_id)
    rows = session.execute(
        select(InboundMessage, User.name)
        .outerjoin(User, User.id == InboundMessage.user_id)
        .where(InboundMessage.project_id == project.id)
        .order_by(InboundMessage.received_at.desc(), InboundMessage.id.desc())
        .limit(SHOWN)
    ).all()
    return MailboxOut(
        available=bool(domain),
        address=address_of(mailbox, domain) if mailbox is not None and domain else None,
        senders=mailbox.senders if mailbox is not None else [],
        folder_id=mailbox.folder_id if mailbox is not None and folder is not None else None,
        folder=folder,
        max_senders=MAX_SENDERS,
        days=INBOUND_DAYS,
        messages=[
            InboundMessageOut(
                id=message.id,
                received_at=message.received_at,
                sender=message.sender,
                member=(name or "a member") if message.user_id is not None else None,
                subject=message.subject,
                outcome=message.outcome,
                reason=message.reason,
                kept=message.kept,
                rejected=[InboundRejectedOut(**rejected) for rejected in message.rejected],
            )
            for message, name in rows
        ],
    )
