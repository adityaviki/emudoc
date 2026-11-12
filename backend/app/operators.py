"""The operator's area: the pages at /operator where the people who run the deployment read, in
a browser, what its maintenance commands print in a shell (app/maintenance.py): the
organisations with their usage and members, the users, what the models cost, the job
queue with its dead letters, and how the models and services stand. The routes are in
app/api/operator.py, the page in frontend/src/operator/, a bundle of its own.

An operator is a user the `operators` command granted it to (the `operators` table, by the
user's id), whatever organisations they are a member of: nobody is one by a setting or a role in
an organisation, and nobody becomes one from a page. A request is let in when it comes:

- from an address OPERATOR_NETWORKS lists, the client's as uvicorn believes the proxy;
- with a session, never an API key (a key acts in its organisation alone), and never as the
  local account of development;
- of a user who is an operator;
- in production, of a user who signs in with two-factor.

Anyone else is answered as if nothing were there: 404 for every path under /api/operator,
whatever the method, and the members' page at /operator, as for any path the app does not
serve, so that the area is not found by trying. An operator without two-factor sign-in alone is
told why they are refused, since the address and the session already show who they are.

What the area shows is what the commands print: names, settings and figures, a member's name
and address where support needs them, never what a document, a question or an answer says.
Reading changes nothing, so it writes nothing to an organisation's audit log; the access log
names the operator by id with every path they read.
"""

import logging
from datetime import datetime
from ipaddress import ip_address

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.apikeys import bearer
from app.auth import SESSION_COOKIE, find_session, touch_session
from app.config import get_settings
from app.models import Operator, User, UserSession

log = logging.getLogger(__name__)

# What an operator without two-factor sign-in is told in production, in place of the area.
TWO_FACTOR_FIRST = (
    "The operator's area needs two-factor sign-in, since whoever holds your session would read "
    "every organisation: turn it on under your account, then open the area again."
)


def from_operator_network(request: Request) -> bool:
    """Whether the request comes from an address OPERATOR_NETWORKS lists."""
    if request.client is None:
        return False
    try:
        address = ip_address(request.client.host)
    except ValueError:
        # Not an address at all, as the test client's "testclient": nobody's network.
        return False
    return any(address in network for network in get_settings().operator_nets)


def operator_session(session: Session, request: Request) -> UserSession | None:
    """The session the request came with, provided it is an operator's from an operator's
    network; None for anyone else, who is shown nothing of the area."""
    if not from_operator_network(request) or bearer(request) is not None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    user_session = find_session(session, token) if token else None
    # A session that still waits for the authenticator code grants nothing yet.
    if user_session is None or user_session.needs_mfa:
        return None
    if not is_operator(session, user_session.user_id):
        return None
    return user_session


def operator_of(session: Session, request: Request) -> User | None:
    """The operator the request is from, as `operator_session` lets one in."""
    user_session = operator_session(session, request)
    if user_session is None:
        return None
    touch_session(session, user_session)
    return session.get(User, user_session.user_id)


def two_factor_due(user: User) -> bool:
    """Whether the operator must turn two-factor sign-in on before the area opens: in
    production, where their session reads every organisation."""
    return get_settings().environment == "production" and not user.mfa_enabled


def is_operator(session: Session, user_id: int) -> bool:
    return session.get(Operator, user_id) is not None


def operators(session: Session) -> list[tuple[User, datetime]]:
    """Every operator with when they were granted it, the earliest first."""
    rows = session.execute(
        select(User, Operator.created_at)
        .join(Operator, Operator.user_id == User.id)
        .order_by(Operator.created_at, User.id)
    )
    return [(user, granted) for user, granted in rows]


def grant(session: Session, user: User) -> bool:
    """Make the user an operator; False when they were one already. The caller commits."""
    if is_operator(session, user.id):
        return False
    session.add(Operator(user_id=user.id))
    log.info("User %s was made an operator", user.id)
    return True


def revoke(session: Session, user: User) -> bool:
    """Take the operator's area from the user; False when they had none. Their sessions go on
    in the members' app. The caller commits."""
    operator = session.get(Operator, user.id)
    if operator is None:
        return False
    session.delete(operator)
    log.info("User %s is no longer an operator", user.id)
    return True
