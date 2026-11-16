"""Who sees which of an organisation's projects.

A project is seen by everyone in its organisation, unless it is private: then only by the
members chosen for it (`project_members`) and the organisation's owners and admins, who see
every project, so that none is left that nobody can manage. A project the account does not see
is not found, as another organisation's is: `get_project` and `_in_organization` in
app/api/deps.py, through which every route reaches a project and what it holds, and the list of
projects, all filter by `visible_to`.

Whoever may change a project, a member or better who sees it, chooses who sees it. A member who
is not an owner or admin stays among a private project's members, since they could not open it
again otherwise; a viewer may be chosen, to read it. Opening a project to everyone forgets its
members, so making it private again starts the choice afresh. The choice is the membership's:
being removed from the organisation takes the user's rows with it, and a role
changed keeps them, so an admin made a member sees the private projects chosen for them alone.

What a member did in a project they no longer see stays theirs: their chats in it are hidden
until they are chosen again. Access is checked when a question is asked, so an answer under way
finishes.

A project in the trash (app/trash.py) is seen through the trash alone: `visible_to` leaves it
out, and `sees`, the same choice without the trash, is what the trash's routes find it by.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, delete, exists, or_, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.models import Membership, Project, ProjectMember

KEEP_YOURSELF = (
    "Keep yourself among the members of a private project: you would no longer see it, and only "
    "an owner or admin of the workspace could open it again."
)
NOT_MEMBERS = (
    "Choose a private project's members among the workspace's: someone chosen is not a "
    "member of it. Invite them to the workspace first."
)


@dataclass(frozen=True)
class AccessChange:
    """What a change to a project's access found, and whom it let in and left out, by user id
    in order."""

    visibility: str
    added: list[int]
    removed: list[int]


def sees_every_project(account: Account) -> bool:
    """Whether the account sees the organisation's private projects whoever was chosen."""
    return account.is_at_least("admin")


def visible_to(account: Account) -> ColumnElement[bool]:
    """The condition a project the account sees meets: one it may see (`sees`) that is not in
    the trash (app/trash.py), where nothing of it is reached but through the trash itself."""
    return and_(sees(account), Project.deleted_at.is_(None))


def sees(account: Account) -> ColumnElement[bool]:
    """The condition a project the account may see meets, in the trash or out of it: one of its
    organisation's that is open to everyone, or private with the user chosen, or any for an
    owner or admin."""
    ours = Project.org_id == account.organization.id
    if sees_every_project(account):
        return ours
    chosen = exists().where(
        ProjectMember.project_id == Project.id, ProjectMember.user_id == account.user.id
    )
    return and_(ours, or_(Project.visibility == "organization", chosen))


def chosen_members(session: Session, project: Project) -> list[int]:
    """The ids of the users chosen to see the project; none for one open to everyone."""
    return list(
        session.scalars(
            select(ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id)
            .order_by(ProjectMember.user_id)
        )
    )


def access_refusal(
    session: Session, account: Account, visibility: str, user_ids: list[int]
) -> str | None:
    """Why the account may not give its organisation's project this access, or None."""
    if visibility != "private":
        return None
    if account.user.id not in user_ids and not sees_every_project(account):
        return KEEP_YOURSELF
    members = set(
        session.scalars(
            select(Membership.user_id).where(
                Membership.org_id == account.organization.id, Membership.user_id.in_(user_ids)
            )
        )
    )
    return None if members == set(user_ids) else NOT_MEMBERS


def set_access(
    session: Session, project: Project, visibility: str, user_ids: list[int]
) -> AccessChange:
    """Make the project open to everyone, or private to the users chosen, whom the caller has
    checked with `access_refusal`, and say whom that changed. Holds the project's row until the
    commit, so that two changes at once take their turns. The caller commits."""
    session.refresh(project, with_for_update=True)
    was = project.visibility
    before = set(chosen_members(session, project))
    after = set(user_ids) if visibility == "private" else set()
    if removed := before - after:
        session.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id.in_(removed)
            )
        )
    session.add_all(
        ProjectMember(project_id=project.id, user_id=user_id) for user_id in after - before
    )
    project.visibility = visibility
    return AccessChange(was, sorted(after - before), sorted(removed))


def forget_projects(session: Session, org_id: int, user_id: int) -> None:
    """Drop the user from the private projects of an organisation they are leaving, so that
    joining it again shows them none. The caller commits."""
    session.execute(
        delete(ProjectMember).where(
            ProjectMember.user_id == user_id,
            ProjectMember.project_id.in_(select(Project.id).where(Project.org_id == org_id)),
        )
    )
