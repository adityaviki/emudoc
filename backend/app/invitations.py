"""How people join an organisation: by an invitation mailed to their address, or by the domain
of their confirmed address.

An owner or admin invites an address with a role no higher than their own
(app/api/invitations.py), and the address is mailed a link to the invitation's page. Whoever
opens it joins with that role: the account with the address, signed in, or a new one made on
the page or through a provider from it, which is then a member of the organisation that invited
it and of none of its own. Opening the link proves the address, so an account that had not
confirmed it has then; an account with another address may not accept it, since the link is
for whoever holds the address it was sent to. The token is random and only its SHA-256 is
kept, as for sessions and one-time links, and sending the invitation again replaces it, so the
link mailed before stops working. An invitation lasts INVITATION_TTL and holds a place among the
members the plan allows while it is open (`check_member` in app/entitlements.py); an address
has at most one to an organisation. Accepting or revoking it deletes it; one that expired stays,
for its inviter to send again, until CREDENTIAL_HISTORY after it expired (`prune_credentials`
in app/auth.py), as a one-time link does.

An organisation may also let everyone whose confirmed address is at one of its domains join it,
as a member or a viewer. The app offers it to them (`joinable`, which `MeOut` lists) and never
makes anyone a member by itself. A domain is added only by an owner or admin whose own confirmed
address is at it, since whoever joins reads the organisation's documents, and never one that
hands addresses out to anyone (SHARED_DOMAINS), at which everyone would be offered it.
"""

import datetime as dt
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import new_token, now, token_hash
from app.models import Invitation, Membership, Organization, User

INVITATION_TTL = dt.timedelta(days=7)
# A host name of labels joined by dots, the last of letters or an IDN's punycode.
DOMAIN = re.compile(
    r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})"
)
# Where anyone may have an address: the large free mail services. Not every one there is, but
# the rule that only someone with a confirmed address at a domain may add it is what holds;
# this keeps a member from offering an organisation to everyone at their mail service.
SHARED_DOMAINS = frozenset(
    {
        "126.com",
        "163.com",
        "aol.com",
        "duck.com",
        "fastmail.com",
        "free.fr",
        "freenet.de",
        "gmail.com",
        "gmx.at",
        "gmx.ch",
        "gmx.com",
        "gmx.de",
        "gmx.net",
        "googlemail.com",
        "hey.com",
        "hotmail.co.uk",
        "hotmail.com",
        "hotmail.de",
        "hotmail.es",
        "hotmail.fr",
        "hotmail.it",
        "icloud.com",
        "interia.pl",
        "laposte.net",
        "libero.it",
        "live.co.uk",
        "live.com",
        "live.de",
        "live.fr",
        "mac.com",
        "mail.com",
        "mail.ru",
        "mailbox.org",
        "me.com",
        "msn.com",
        "naver.com",
        "o2.pl",
        "onet.pl",
        "orange.fr",
        "outlook.com",
        "outlook.de",
        "outlook.fr",
        "pm.me",
        "posteo.de",
        "proton.me",
        "protonmail.com",
        "qq.com",
        "rocketmail.com",
        "seznam.cz",
        "t-online.de",
        "tuta.io",
        "tutanota.com",
        "virgilio.it",
        "web.de",
        "wp.pl",
        "yahoo.co.uk",
        "yahoo.com",
        "yahoo.de",
        "yahoo.es",
        "yahoo.fr",
        "yahoo.it",
        "yandex.com",
        "yandex.ru",
        "ymail.com",
        "zoho.com",
    }
)


def domain_of(email: str) -> str:
    """The domain an address is at."""
    return email.rpartition("@")[2].strip().lower()


def issue(invitation: Invitation) -> str:
    """Give the invitation a new token and INVITATION_TTL from now, and return the token for its
    link; the link mailed before stops working. The caller commits."""
    token = new_token()
    invitation.token_hash = token_hash(token)
    invitation.expires_at = now() + INVITATION_TTL
    return token


def find_invitation(session: Session, token: str, lock: bool = False) -> Invitation | None:
    """The invitation a link's token names, open or expired; None when there is none, as once
    it was accepted, revoked or sent again. `lock` holds the row until the commit, so that two
    acceptances at once make one member."""
    statement = select(Invitation).where(Invitation.token_hash == token_hash(token))
    return session.scalar(statement.with_for_update() if lock else statement)


def is_open(invitation: Invitation) -> bool:
    return invitation.expires_at > now()


def joining_refusal(domain: str, user: User) -> str | None:
    """Why the user may not let everyone at `domain` join their organisation, or None when
    they may: it must be the domain of their own confirmed address, and not a mail service's."""
    if not DOMAIN.fullmatch(domain):
        return f"{domain} is not a domain. Type one such as acme.com."
    if domain in SHARED_DOMAINS:
        return (
            f"Anyone can have an address at {domain}, so its addresses say nothing of who works "
            "with you. Invite people one by one instead."
        )
    if user.email_verified_at is None or domain_of(user.email) != domain:
        return (
            f"Only someone whose own confirmed address is at {domain} can let everyone at it "
            "join, since whoever joins reads the organisation's documents. Ask a member with "
            "such an address to add it."
        )
    return None


def joinable(session: Session, user: User) -> list[Organization]:
    """The organisations the user may join by the domain of their address, by name: none until
    they have confirmed it, and none set to be deleted (app/retention.py)."""
    if user.email_verified_at is None:
        return []
    member_of = select(Membership.org_id).where(Membership.user_id == user.id)
    return list(
        session.scalars(
            select(Organization)
            .where(
                Organization.join_domains.contains([domain_of(user.email)]),
                Organization.id.not_in(member_of),
                Organization.deletion_due_at.is_(None),
            )
            .order_by(func.lower(Organization.name), Organization.id)
        )
    )
