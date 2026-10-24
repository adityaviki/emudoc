"""Documents' reviews: the accounts payable's way with an invoice, open to a document of any kind.

A member puts a document in review, assigns it to a member who may change the project, and
approves it or disputes it, the states of REVIEW_STATES. A document is in no review until then,
and most never are: a contract or a letter is read and asked about, not approved. Assigning a
document in no review puts it in review, since being assigned is being asked to look at it, and
taking a document out of review drops its assignee, so that nobody is left assigned what nobody
reviews any more (the checks on `documents` hold both).

An automation's run may put in review the documents it was for (`put_in_review`,
AUTOMATION_REVIEWS: those its cross-checks found a difference about, or every one that arrived),
assigned to the member it names. It never moves a document already in review, approved or
disputed: a member's word is never the machine's to change, and the notice of its run tells what
it found about it all the same.

The state is a member's word on the document as a whole, never worked out from its facts. The
facts table's checks (app/fact_checks.py) are a member's word on one value the model read, and
its "Unchecked" is the documents with a value nobody checked: work towards a review, which may
be done or not when the document is approved. Approving an invoice whose total differs from its
order is the member's call, which the cross-checks (app/cross_checks.py) inform and never make.

Whoever may change the project moves a review, so a viewer reads where each stands and changes
none; who moved it to its state last and when is kept on the document, and each change, and each
assignment, is a row of the organisation's audit log by the document's name and the states or
the assignee's id, never a value it states. A member assigned by another is told (`tell_assigned`,
the notice `review_assigned`). A member who leaves the organisation, or is removed from it, loses
their assignments in its projects (`forget_assignments`), and an account purged leaves its
documents assigned to nobody; a role lowered to viewer, or a project made private without them,
keeps the assignment, which whoever may change the project hands to another.

A document in the trash (app/trash.py) keeps its review and comes back with it, and the reviews
view lists the live ones alone. A project's export (app/export.py) names each document's review.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.mail import automation_review_email, in_organization, review_assigned_email
from app.models import (
    REVIEW_STATES,
    ROLES,
    Automation,
    Document,
    Membership,
    Project,
    ProjectMember,
    User,
)
from app.notices import PROJECT_PAGE, REVIEW_ASSIGNED, tell

TO_REVIEW, APPROVED, DISPUTED = REVIEW_STATES
# Each state as a file the members download words it (app/fact_export.py), as the page does.
STATE_WORDS = {TO_REVIEW: "To review", APPROVED: "Approved", DISPUTED: "Disputed"}
# The roles a document may be assigned to: those that may change it.
REVIEWER_ROLES = ROLES[:3]
# The states a review is still open in, listed oldest first: what has waited longest comes
# first. An approved one is done, and listed newest first.
OPEN = (TO_REVIEW, DISPUTED)

NOT_A_REVIEWER = (
    "Assign it to a member of the organisation who sees this project and may change it: a viewer "
    "cannot move a review."
)
NOBODY_OUT_OF_REVIEW = (
    "A document in no review is assigned to nobody. Keep it in review to assign it, or assign "
    "nobody."
)
BAD_CURSOR = "This page of the reviews cannot be found. List them again from the start."
# Where a notice of a document assigned leads: the document in its project, or for several the
# member's own in the reviews view.
DOCUMENT_PAGE = PROJECT_PAGE + "/documents/{}"
MY_REVIEWS_PAGE = PROJECT_PAGE + "?reviews=mine"


@dataclass(frozen=True)
class ReviewChange:
    """What a change to a document's review did: its state and its assignee's id, before and
    after."""

    state_from: str | None
    state_to: str | None
    assignee_from: int | None
    assignee_to: int | None

    @property
    def moved(self) -> bool:
        return self.state_from != self.state_to

    @property
    def assigned(self) -> bool:
        return self.assignee_from != self.assignee_to


def reviewer_condition(project: Project) -> ColumnElement[bool]:
    """The condition a membership of the project's organisation meets when its user may be
    assigned the project's documents: a role that may change them, and the project seen."""
    held = [Membership.org_id == project.org_id, Membership.role.in_(REVIEWER_ROLES)]
    if project.visibility != "organization":
        chosen = exists().where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == Membership.user_id
        )
        held.append(or_(Membership.role.in_(ROLES[:2]), chosen))
    return and_(*held)


def reviewers(session: Session, project: Project) -> list[tuple[int, str]]:
    """The members the project's documents may be assigned to, by name: id and name."""
    return [
        (user_id, name)
        for user_id, name in session.execute(
            select(User.id, User.name)
            .join(Membership, Membership.user_id == User.id)
            .where(reviewer_condition(project))
            .order_by(func.lower(User.name), User.id)
        )
    ]


def may_review(session: Session, project: Project, user_id: int) -> bool:
    return session.scalar(
        select(exists().where(Membership.user_id == user_id, reviewer_condition(project)))
    )


class ReviewRefused(ValueError):
    """A change a review may not take, in the sentence the member is shown."""


def change_review(
    session: Session,
    project: Project,
    document: Document,
    given: set[str],
    state: str | None,
    assignee_id: int | None,
    by: int | None,
) -> ReviewChange:
    """Move the document's review as a change asks, from the fields it gave (`given`), the others
    staying as they are, `by` the user who did, None for an automation. An assignee for a
    document in no review puts it in review, and taking it out of review drops the assignee; an
    assignee who may not review it is refused (ReviewRefused), unless kept as they were, so that
    a document assigned to a member since made a viewer still moves. The moment and who are kept
    when the state changes, and forgotten with the review. The document's row is held first,
    until the commit, so that two changes at once take their turns, each read against the
    other's. The caller commits."""
    session.refresh(document, with_for_update=True)
    if "state" in given and state is None and assignee_id is not None:
        raise ReviewRefused(NOBODY_OUT_OF_REVIEW)
    if "state" not in given:
        state = document.review
        if "assignee_id" in given and assignee_id is not None and state is None:
            state = TO_REVIEW
    if "assignee_id" not in given or state is None:
        assignee_id = document.assignee_id if state is not None else None
    if (
        assignee_id is not None
        and assignee_id != document.assignee_id
        and not may_review(session, project, assignee_id)
    ):
        raise ReviewRefused(NOT_A_REVIEWER)
    change = ReviewChange(document.review, state, document.assignee_id, assignee_id)
    if change.moved:
        document.review = state
        document.review_changed_by = by if state is not None else None
        document.review_changed_at = datetime.now(UTC) if state is not None else None
    document.assignee_id = assignee_id
    return change


def tell_assigned(
    session: Session,
    project: Project,
    document: Document,
    assignee_id: int,
    assigner: str,
    app_url: str,
) -> bool:
    """Tell the member another assigned the document's review, in the caller's session, as they
    chose for the kind of notice; returns whether they were told either way."""
    user = session.get(User, assignee_id)
    organization = project.organization
    page = DOCUMENT_PAGE.format(project.id, document.id)
    content = review_assigned_email(
        user.name,
        assigner,
        document.filename,
        document.review,
        project.name,
        organization.name,
        in_organization(f"{app_url}{page}", organization.id),
    )
    return tell(session, user, organization.id, REVIEW_ASSIGNED, content, page, app_url)


def put_in_review(
    session: Session,
    project: Project,
    document_ids: list[int],
    assignee_id: int | None,
    automation: str,
    app_url: str,
) -> list[str]:
    """Put in review, for the automation named, the project's documents of `document_ids` out of
    the trash that are in none, assigned to `assignee_id` while they may still review them, else
    to nobody; each a row of the audit log by the app, and the assignee told once of them all.
    Returns the names of the files it put in review. One statement, so that a document a member
    moves meanwhile is theirs. The caller commits."""
    if assignee_id is not None and not may_review(session, project, assignee_id):
        assignee_id = None
    put = session.execute(
        update(Document)
        .where(
            Document.id.in_(document_ids),
            Document.project_id == project.id,
            Document.deleted_at.is_(None),
            Document.review.is_(None),
        )
        .values(
            review=TO_REVIEW,
            assignee_id=assignee_id,
            review_changed_by=None,
            review_changed_at=func.now(),
        )
        .returning(Document.id, Document.filename)
        .execution_options(synchronize_session=False)
    ).all()
    put.sort()
    for document_id, filename in put:
        target = ("document", document_id)
        named = {"filename": filename, "automation": automation}
        audit_event(
            session,
            "document_review_changed",
            org_id=project.org_id,
            project_id=project.id,
            target=target,
            detail={**named, "from": None, "to": TO_REVIEW},
        )
        if assignee_id is not None:
            audit_event(
                session,
                "document_assigned",
                org_id=project.org_id,
                project_id=project.id,
                target=target,
                detail={**named, "from": None, "to": assignee_id},
            )
    if put and assignee_id is not None:
        user = session.get(User, assignee_id)
        organization = project.organization
        page = (
            DOCUMENT_PAGE.format(project.id, put[0][0])
            if len(put) == 1
            else MY_REVIEWS_PAGE.format(project.id)
        )
        content = automation_review_email(
            user.name,
            automation,
            [filename for _, filename in put],
            project.name,
            organization.name,
            in_organization(f"{app_url}{page}", organization.id),
        )
        tell(session, user, organization.id, REVIEW_ASSIGNED, content, page, app_url)
    return [filename for _, filename in put]


def forget_assignments(session: Session, org_id: int, user_id: int) -> None:
    """Assign nobody the documents of the organisation's projects that were assigned to the
    user, who is leaving it, nor what its automations put in review, so that joining it again
    finds none theirs. The caller commits."""
    session.execute(
        update(Document)
        .where(
            Document.assignee_id == user_id,
            Document.project_id.in_(select(Project.id).where(Project.org_id == org_id)),
        )
        .values(assignee_id=None)
    )
    session.execute(
        update(Automation)
        .where(Automation.org_id == org_id, Automation.assignee_id == user_id)
        .values(assignee_id=None)
    )


@dataclass(frozen=True)
class Reviews:
    documents: list[Document]
    counts: dict[str, int]
    next_after: str | None


def read_reviews(
    session: Session,
    project_id: int,
    state: str,
    *,
    assignee_id: int | None = None,
    after: str | None = None,
    limit: int = 100,
) -> Reviews:
    """A page of the project's live documents in review of `state`, assigned to `assignee_id`
    alone when given, past `after`: an open review oldest first, an approved one newest first,
    by the moment it was moved to its state and then by id; and how many there are of each
    state, so held, over every page. Raises ValueError with BAD_CURSOR for a cursor it did not
    write."""
    held = [
        Document.project_id == project_id,
        Document.review.is_not(None),
        Document.deleted_at.is_(None),
    ]
    if assignee_id is not None:
        held.append(Document.assignee_id == assignee_id)
    counts = {name: 0 for name in REVIEW_STATES}
    counts.update(
        session.execute(
            select(Document.review, func.count()).where(*held).group_by(Document.review)
        ).all()
    )
    key = tuple_(Document.review_changed_at, Document.id)
    newest_first = state not in OPEN
    query = select(Document).where(*held, Document.review == state)
    if after is not None:
        moment, document_id = _cursor(after)
        past = tuple_(moment, document_id)
        query = query.where(key < past if newest_first else key > past)
    if newest_first:
        query = query.order_by(Document.review_changed_at.desc(), Document.id.desc())
    else:
        query = query.order_by(Document.review_changed_at, Document.id)
    found = list(session.scalars(query.limit(limit + 1)))
    shown = found[:limit]
    return Reviews(
        documents=shown,
        counts=counts,
        next_after=_cursor_of(shown[-1]) if len(found) > limit else None,
    )


def _cursor_of(document: Document) -> str:
    encoded = json.dumps([document.review_changed_at.isoformat(), document.id]).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _cursor(after: str) -> tuple[datetime, int]:
    try:
        moment, document_id = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
        moment = datetime.fromisoformat(moment)
    except (ValueError, TypeError):
        raise ValueError(BAD_CURSOR) from None
    if moment.tzinfo is None or not isinstance(document_id, int) or not 0 < document_id < 2**31:
        raise ValueError(BAD_CURSOR)
    return moment, document_id
