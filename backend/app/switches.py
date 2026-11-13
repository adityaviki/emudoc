"""The kill switches the workspace's owners and admins flip from its Administration page
(app/api/admin.py): a model provider switched off in every process of the install within
SWITCH_SECONDS, and on again, with no restart. For a bill that is running away or a key that
leaked, when the day's spending limit is too far off.

DISABLED_PROVIDERS (app/config.py) is the deployment's own switch, read at start, and nothing on
a page turns it back on: a provider is off while either names it. The table
(`provider_switches`, a row for each provider switched off) is read by `LLM._model` before a
model is asked, through `switched_off`, which keeps what it read for SWITCH_SECONDS, so that a
process asks the database once in that time however many requests it makes. A process that
cannot read it keeps what it read last and says so in its log, since a database that is down
stops more than the models.

Everything that asks whether the models can be used reads the same: `check_model` at start,
`LLM.problem()` and so `/api/status`, the worker's watch and the page's services. The `models`
maintenance command connects to no database and asks every model whatever the table says, which
is how an admin tries a provider before switching it back on.

The table keeps who switched a provider off and when; the route writes each switch into the
workspace's audit log (`provider_switched`), and the log here names the user by id.
"""

import logging
import threading
import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.errors import summary
from app.models import ProviderSwitch, User

log = logging.getLogger(__name__)

# How long a process keeps what it read of the switches: how long a switch takes to reach
# every process, and how often each asks the database at most.
SWITCH_SECONDS = 5.0


class Switches:
    """The providers switched off in the table, as a process last read them: called before a
    model is asked, it reads the table again once SWITCH_SECONDS have passed. While one thread
    reads, the others take what was read before rather than wait on the database."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._off: frozenset[str] = frozenset()
        self._until = 0.0

    def __call__(self) -> frozenset[str]:
        if time.monotonic() < self._until or not self._lock.acquire(blocking=False):
            return self._off
        try:
            if time.monotonic() >= self._until:
                self._off = self._read()
                self._until = time.monotonic() + SWITCH_SECONDS
        finally:
            self._lock.release()
        return self._off

    def forget(self) -> None:
        """Read the table again at the next call: after this process switched one itself."""
        self._until = 0.0

    def _read(self) -> frozenset[str]:
        try:
            with SessionLocal() as session:
                return frozenset(session.scalars(select(ProviderSwitch.provider)))
        except SQLAlchemyError as error:
            log.warning(
                "The providers switched off on the Administration page could not be read, so those "
                "read before still hold: %s",
                summary(error),
            )
            return self._off


# The process's switches, which the LLM built at start reads (app/startup.py).
switched_off = Switches()


def switches(session: Session) -> list[tuple[ProviderSwitch, User | None]]:
    """Every provider switched off in the table, with the user who did it (None once their
    account is gone), by name."""
    rows = session.execute(
        select(ProviderSwitch, User)
        .outerjoin(User, User.id == ProviderSwitch.user_id)
        .order_by(ProviderSwitch.provider)
    )
    return [(switch, user) for switch, user in rows]


def switch_off(session: Session, provider: str, user: User) -> bool:
    """Switch `provider` off for every process; False when it was off already. The caller
    commits, then calls `switched_off.forget()`."""
    added = session.execute(
        insert(ProviderSwitch)
        .values(provider=provider, user_id=user.id)
        .on_conflict_do_nothing()
        .returning(ProviderSwitch.provider)
    ).first()
    if added is not None:
        log.warning("User %s switched the model provider %s off", user.id, provider)
    return added is not None


def switch_on(session: Session, provider: str, user: User) -> bool:
    """Switch `provider` on again, unless DISABLED_PROVIDERS holds it off; False when the table
    did not hold it off. The caller commits, then calls `switched_off.forget()`."""
    removed = session.execute(
        delete(ProviderSwitch)
        .where(ProviderSwitch.provider == provider)
        .returning(ProviderSwitch.provider)
    ).first()
    if removed is not None:
        log.warning("User %s switched the model provider %s on again", user.id, provider)
    return removed is not None
