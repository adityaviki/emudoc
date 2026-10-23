"""Feature flags: a feature held back from some organisations while it is rolled out. Tier 3's
features (EMUDOC_SAAS_CHECKLIST.md) are built behind one, turned on first for the organisations
an operator chooses, then for a growing share of the rest, then for everyone, when the flag and
its reads are taken out of the code.

A flag is an entry of FLAGS, the registry: its name, what it turns on, and its share, the
percentage of organisations it is on for unless an operator chose otherwise for one. Which
organisations a share takes is a hash of the flag's name and the organisation's id
(`in_share`), so that raising the share keeps those it took and adds others, and two flags at
the same share take different ones. The share is the code's, moved by a release; an
organisation's own choice is the operator's, made in the operator's area or by the `flags`
command, kept in `organizations.flags` (`{name: true | false}`, a name left out following the
share) and written to the organisation's audit log (`choose`).

A flag differs per organisation, so it is never a setting (app/config.py): it is read through
the organisation, `flag_on(organization, name)`, which the API finds in the request's account
and the worker by a job's organisation (`flag_on_for`); the page reads `OrganizationOut.flags_on`.
A name FLAGS does not hold is refused wherever it is read (`UnknownFlag`), and by `flagged(name)`
in app/api/deps.py, the dependency that hides a route where its flag is off, as its module is
imported: a misspelt flag fails its tests rather than turning nothing on. The choices of a flag
taken out of FLAGS stay in the column, read by nothing, so its name is never given to another.
"""

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.models import Organization

# What a flag's name may be, since it is a key of `organizations.flags`, a part of the operator's
# area's paths and a word of the audit log.
NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class Flag:
    """A feature behind a flag: what it turns on, in a few words for the operator's area, and the
    percentage of organisations it is on for unless an operator chose for one."""

    title: str
    share: int = 0

    def __post_init__(self) -> None:
        if type(self.share) is not int or not 0 <= self.share <= 100:
            raise ValueError(f"A flag's share is a percentage from 0 to 100, not {self.share!r}.")


# The flags whose names the code reads, each where its feature is.
EXTRACTION_TEMPLATES = "extraction_templates"

# Every feature behind a flag, by name, in the order the operator's area lists them.
FLAGS: dict[str, Flag] = {
    # A project's extraction templates (app/extraction.py): the fields its documents of each
    # kind are read for, the built-in ones' among them, the page that changes them, and the
    # facts table that shows a kind's documents side by side (app/fact_table.py).
    EXTRACTION_TEMPLATES: Flag(
        "Extraction templates and the facts table: the fields each kind of document is read "
        "for, and its documents side by side"
    ),
}


class UnknownFlag(LookupError):
    """A name FLAGS does not hold: a misspelling, or a flag taken out."""


def flag(name: str) -> Flag:
    """The flag of that name; raises UnknownFlag for a name FLAGS does not hold."""
    found = FLAGS.get(name)
    if found is None:
        raise UnknownFlag(f"No feature flag is named {name!r}; FLAGS in app/flags.py names them.")
    return found


def in_share(name: str, org_id: int, share: int) -> bool:
    """Whether a flag rolled out to `share` percent of the organisations takes this one."""
    digest = hashlib.sha256(f"{name}:{org_id}".encode()).digest()
    return int.from_bytes(digest[:8]) % 100 < share


def chosen(organization: Organization, name: str) -> bool | None:
    """What an operator chose for the organisation: on, off, or None for the flag's share."""
    choice = (organization.flags or {}).get(name)
    return choice if isinstance(choice, bool) else None


def flag_on(organization: Organization, name: str) -> bool:
    """Whether the feature behind the flag is on for the organisation: as an operator chose for
    it, else as the flag's share takes it."""
    share = flag(name).share
    choice = chosen(organization, name)
    return in_share(name, organization.id, share) if choice is None else choice


def flag_on_for(session: Session, org_id: int, name: str) -> bool:
    """`flag_on` for an organisation known by its id, as the worker knows a job's."""
    return flag_on(session.get_one(Organization, org_id), name)


def flags_on(organization: Organization) -> list[str]:
    """The flags on for the organisation, in the order of FLAGS, for its page."""
    return [name for name in FLAGS if flag_on(organization, name)]


def choose(
    session: Session,
    organization: Organization,
    name: str,
    on: bool | None,
    *,
    operator: int | None = None,
) -> bool:
    """Turn the flag on or off for the organisation, or back to the flag's share (None), with a
    row of its audit log naming the operator by id when one did it from the area; False when
    that was the choice already. The caller holds the organisation's row and commits."""
    flag(name)
    was = chosen(organization, name)
    if was is on:
        return False
    flags = {key: value for key, value in (organization.flags or {}).items() if key != name}
    if on is not None:
        flags[name] = on
    organization.flags = flags
    detail: dict[str, object] = {
        "flag": name,
        "from": was,
        "to": on,
        "on": flag_on(organization, name),
    }
    if operator is not None:
        detail["operator"] = operator
    audit_event(session, "flag_changed", org_id=organization.id, detail=detail)
    return True


@dataclass(frozen=True)
class Rollout:
    """A flag across the deployment: how many organisations it is on for of all of them, and
    those an operator chose for, each as (id, slug, name, on), by name."""

    name: str
    flag: Flag
    on: int
    organizations: int
    chosen: list[tuple[int, str, str, bool]]


def rollouts(session: Session) -> list[Rollout]:
    """Every flag of FLAGS as it stands across the deployment, which the `flags` command and the
    operator's area show: one read of the organisations, whose shares are worked out here."""
    if not FLAGS:
        return []
    rows = session.execute(
        select(Organization.id, Organization.slug, Organization.name, Organization.flags).order_by(
            Organization.name, Organization.id
        )
    ).all()
    found = []
    for name, entry in FLAGS.items():
        on, picked = 0, []
        for org_id, slug, named, flags in rows:
            choice = (flags or {}).get(name)
            if isinstance(choice, bool):
                picked.append((org_id, slug, named, choice))
                on += choice
            else:
                on += in_share(name, org_id, entry.share)
        found.append(Rollout(name, entry, on, len(rows), picked))
    return found
