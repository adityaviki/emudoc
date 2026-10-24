"""What the database keeps encrypted (app/keyring.py), and its upkeep.

`COLUMNS` names each column whose values would let a copy of the database act as a member: the
authenticators' secrets of two-factor sign-in, and the grants members gave to read the folders
they connected (app/connectors/). The code that writes such a value
encrypts it for its row (`encrypt`) and the code that reads it decrypts it (`decrypt`), so the
rows hold nothing a dump or a backup can use without ENCRYPTION_KEYS. A value kept as it was
before its column was encrypted still reads as it is, until `encrypt_secrets` encrypts it.

`encrypt_secrets` is the `encrypt-secrets` command: it encrypts with the first key every value
that is not yet, those kept as they were and those of an earlier key, which is the second half
of a key's rotation (deploying.md, "The encryption keys"). It runs beside the app: a row is
written only if it still holds what was read, so a member who sets up an authenticator
meanwhile keeps the new one. At every start `check_encrypted` says which key encrypts and what
is left to do; nothing there refuses a start, since a key lost for some rows must not stop the
others.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from app.config import get_settings
from app.db import engine
from app.keyring import (
    DEVELOPMENT_KEY,
    PREFIX,
    Keyring,
    Undecryptable,
    is_encrypted,
    key_id,
    key_of,
    keyring,
)

log = logging.getLogger(__name__)

COMMAND = "python -m app.maintenance encrypt-secrets"
# How many rows one transaction of the command reads and writes.
BATCH = 500
DEVELOPMENT_ID = key_id(DEVELOPMENT_KEY)


@dataclass(frozen=True)
class Column:
    """A column of encrypted values, each encrypted for its row."""

    table: str
    column: str
    # What one value is, for the sentences: "authenticator secret".
    noun: str
    # What a value no key decrypts keeps from its member, for one and for several, and how they
    # do without it, for the sentences.
    lost_one: str
    lost_many: str
    remedy: str

    def place(self, row_id: int) -> str:
        """Where a value is kept, which it is encrypted for. It is part of every value the
        column holds, so it never changes."""
        return f"{self.table}.{self.column}:{row_id}"


TOTP_SECRETS = Column(
    "users",
    "totp_secret",
    "authenticator secret",
    "its member cannot sign in with an authenticator app",
    "their members cannot sign in with authenticator apps",
    "a member signs in with a recovery code and sets two-factor sign-in up again",
)
CONNECTOR_TOKENS = Column(
    "connectors",
    "refresh_token",
    "connector token",
    "its folder is not synced",
    "their folders are not synced",
    "the member who connected a folder connects it again",
)
COLUMNS = (TOTP_SECRETS, CONNECTOR_TOKENS)


def keys() -> Keyring:
    """The keys this process holds, from ENCRYPTION_KEYS."""
    settings = get_settings()
    return keyring(settings.encryption_keys, settings.environment == "development")


def encrypt(column: Column, row_id: int, value: str) -> str:
    return keys().encrypt(value, column.place(row_id))


def decrypt(column: Column, row_id: int, value: str) -> str:
    """The value as it was given to `encrypt`; one kept as it was before its column was
    encrypted is returned as it is. `Undecryptable` when no key held decrypts it."""
    if not is_encrypted(value):
        return value
    return keys().decrypt(value, column.place(row_id))


@dataclass
class Standing:
    """How a column's values are kept: as they were, with the first key, with an earlier one
    that is still held, or with a key that is not, by fingerprint."""

    column: Column
    plain: int = 0
    current: int = 0
    earlier: int = 0
    lost: Counter[str] = field(default_factory=Counter)

    @property
    def stale(self) -> int:
        """The values `encrypt-secrets` would encrypt afresh."""
        return self.plain + self.earlier


def standing(connection: Connection, ring: Keyring) -> list[Standing]:
    """Each column's standing, by the fingerprints its values carry; nothing is decrypted."""
    found = []
    for column in COLUMNS:
        counts = Standing(column)
        rows = connection.execute(
            text(
                f"SELECT CASE WHEN {column.column} LIKE :prefix "
                f"THEN split_part({column.column}, '.', 2) END AS key, count(*) "
                f"FROM {column.table} WHERE {column.column} IS NOT NULL GROUP BY 1"
            ),
            {"prefix": f"{PREFIX}%"},
        )
        for fingerprint, count in rows:
            if fingerprint is None:
                counts.plain += count
            elif fingerprint == ring.current:
                counts.current += count
            elif ring.holds(fingerprint):
                counts.earlier += count
            else:
                counts.lost[fingerprint] += count
        found.append(counts)
    return found


def check_encrypted() -> None:
    """Say at start which key encrypts, and warn about the values left to encrypt and about
    those whose key is gone."""
    settings = get_settings()
    ring = keys()
    with engine.connect() as connection:
        found = standing(connection, ring)
    which = (
        "the first of ENCRYPTION_KEYS"
        if settings.encryption_keys.strip()
        else "development's own, since ENCRYPTION_KEYS is not set"
    )
    log.info("Secrets in the database are encrypted with key %s, %s.", ring.current, which)
    for counts in found:
        if counts.stale:
            log.warning(
                "%s; run `%s` to encrypt %s with the first key of ENCRYPTION_KEYS.",
                _stale(counts),
                COMMAND,
                "it" if counts.stale == 1 else "them",
            )
        if counts.lost:
            log.warning(lost_line(counts))


@dataclass
class Encrypted:
    """What `encrypt_secrets` did to one column."""

    column: Column
    plain: int = 0
    earlier: int = 0
    lost: Counter[str] = field(default_factory=Counter)


def encrypt_secrets() -> list[Encrypted]:
    """Encrypt with the first key every value that is not yet: kept as it was, or encrypted
    with an earlier key. A value no key held decrypts is left as it is and counted."""
    ring = keys()
    done = []
    with engine.connect() as connection:
        for column in COLUMNS:
            outcome = Encrypted(column)
            last = 0
            while True:
                with connection.begin():
                    rows = connection.execute(
                        text(
                            f"SELECT id, {column.column} FROM {column.table} "
                            f"WHERE {column.column} IS NOT NULL "
                            f"AND {column.column} NOT LIKE :current AND id > :last "
                            f"ORDER BY id LIMIT {BATCH}"
                        ),
                        {"current": f"{PREFIX}{ring.current}.%", "last": last},
                    ).all()
                    for row_id, value in rows:
                        place = column.place(row_id)
                        try:
                            plain = ring.decrypt(value, place) if is_encrypted(value) else value
                        except Undecryptable as error:
                            outcome.lost[error.key_id] += 1
                            continue
                        written = connection.execute(
                            text(
                                f"UPDATE {column.table} SET {column.column} = :new "
                                f"WHERE id = :id AND {column.column} = :old"
                            ),
                            {"new": ring.encrypt(plain, place), "id": row_id, "old": value},
                        ).rowcount
                        if written and key_of(value) is None:
                            outcome.plain += 1
                        elif written:
                            outcome.earlier += 1
                if not rows:
                    break
                last = rows[-1][0]
            done.append(outcome)
    return done


def describe_encrypted(done: list[Encrypted], ring: Keyring, backup_days: int | None) -> list[str]:
    """What `encrypt_secrets` did, a line each, for the command to print."""
    lines = []
    for outcome in done:
        noun = outcome.column.noun
        kinds = []
        if outcome.plain:
            kinds.append(f"{_count(outcome.plain, noun)} kept as {_was(outcome.plain)}")
        if outcome.earlier:
            kinds.append(f"{_count(outcome.earlier, noun)} of an earlier key")
        if kinds:
            lines.append(
                f"Encrypted {' and '.join(kinds)} with key {ring.current}, the first of "
                "ENCRYPTION_KEYS."
            )
        else:
            lines.append(f"Every {noun} is encrypted with key {ring.current} already.")
        if outcome.lost:
            lines.append(lost_line(Standing(outcome.column, lost=outcome.lost)))
    if any(name != DEVELOPMENT_ID for name, _ in ring.ciphers[1:]):
        until = f", BACKUP_DAYS ({backup_days} days) from now" if backup_days else ""
        lines.append(
            "Keep the other keys of ENCRYPTION_KEYS until no backup of the database holds what "
            f"they encrypted{until}, since a restore brings those values back as they were; "
            "then remove them."
        )
    return lines


def lost_line(counts: Standing) -> str:
    """The sentence for the values no key held decrypts."""
    total = counts.lost.total()
    one = total == 1
    keys_named = ", ".join(sorted(counts.lost))
    column = counts.column
    return (
        f"{_count(total, column.noun)} {'is' if one else 'are'} encrypted with "
        f"{'a key' if len(counts.lost) == 1 else 'keys'} ENCRYPTION_KEYS does not hold "
        f"({keys_named}), so {column.lost_one if one else column.lost_many} until the key is "
        f"back in the list, after the first; without it, {column.remedy}."
    )


def _stale(counts: Standing) -> str:
    noun = counts.column.noun
    kinds = []
    if counts.plain:
        kinds.append(
            f"{_count(counts.plain, noun)} {'is' if counts.plain == 1 else 'are'} kept as "
            f"{_was(counts.plain)}, unencrypted"
        )
    if counts.earlier:
        kinds.append(
            f"{_count(counts.earlier, noun)} {'is' if counts.earlier == 1 else 'are'} encrypted "
            "with an earlier key of ENCRYPTION_KEYS"
        )
    return " and ".join(kinds)


def _was(count: int) -> str:
    return "it was" if count == 1 else "they were"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
