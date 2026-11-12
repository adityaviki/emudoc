"""How people join the workspace: by an invitation sent to their address.

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
in app/auth.py), as a one-time link does. A member removed from the workspace keeps their
account, and an invitation to its address is how it comes back (app/api/auth.py).
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import new_token, now, token_hash
from app.models import Invitation

INVITATION_TTL = dt.timedelta(days=7)


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
