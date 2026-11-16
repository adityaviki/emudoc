"""Idempotency keys: how a program sends a request again after losing its answer, without the
request being carried out twice.

A request to the versioned API (app/api/v1/) that makes something (a project, a folder, an
upload, a chat, a question) may carry an `Idempotency-Key` header: a
string of the program's own, a UUID say, of at most MAX_LENGTH characters, as the IETF's draft of
the header has it (draft-ietf-httpapi-idempotency-key-header). The first request with a key
claims it for the API key that sent it, in a row committed before the work begins, with the
request's fingerprint: the SHA-256 of its method, path and query and of what it asked, an
upload's files by their SHA-256 (`fingerprint`). Once the work is done its answer is kept on the
row, and a request sent again with the key is given that answer, marked `Idempotent-Replayed:
true`, with nothing done again. Besides:

- the same key with another request is refused with 422 (KEY_REUSED), which says to send a new
  key with each new request;
- the same key while the request that claimed it is still being carried out is refused with 409
  (IN_FLIGHT) and a Retry-After, after which the answer is there;
- a request that is refused or fails gives its key back, the claim deleted, since it made nothing
  a second try would make twice: a route that commits once has committed nothing, and an
  upload's files kept before its failure are found by the second try as files the project holds
  already (`duplicate_of`, app/uploads.py);
- a claim whose request never ended, its process stopped mid-way, is taken over by the next
  request with the key once it is ABANDONED old. The answer is kept in a commit after the work's,
  so a process that stops between the two leaves such a claim too, and its request is carried
  out twice: the one window where that happens, the length of a commit.

The rows go with their API key, by the database's cascade (app/models.py), and the housekeeper
removes each KEPT after it was claimed (`prune_idempotency_keys`), after which its key is new
again. A key is the program's own text, so nothing logs it.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Connection, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import IdempotencyKey

log = logging.getLogger(__name__)

HEADER = "Idempotency-Key"
# The header an answer given again carries, as Stripe's API has it.
REPLAYED = "Idempotent-Replayed"
MAX_LENGTH = 255
# How long a key's answer is kept for a request sent again, and how long a claim waits for its
# request to end before the next request with the key takes it over.
KEPT = timedelta(hours=24)
ABANDONED = timedelta(minutes=10)
# When to send a request again that found its key's first request still being carried out.
IN_FLIGHT_RETRY = 5

BAD_KEY = (
    f"An Idempotency-Key is 1 to {MAX_LENGTH} printable ASCII characters, such as a UUID. Send "
    "one like that, or none."
)
KEY_REUSED = (
    "This Idempotency-Key was sent before with a different request. Send a new key with each "
    "new request, and a key again only with the request it was first sent with."
)
IN_FLIGHT = (
    "The request first sent with this Idempotency-Key is still being carried out. Send it again "
    "in a moment for its answer."
)

_PRUNE = text("DELETE FROM idempotency_keys WHERE created_at < :before")


def key_of(header: str | None) -> str | None:
    """The key a request's header names, None without one. The draft writes it as a quoted
    string, and most clients send it bare; either is taken, and anything else refused."""
    if header is None:
        return None
    key = header.strip()
    if len(key) >= 2 and key[0] == key[-1] == '"':
        key = key[1:-1]
    if not 0 < len(key) <= MAX_LENGTH or not all(" " <= c <= "~" for c in key):
        raise HTTPException(400, BAD_KEY)
    return key


def fingerprint(request: Request, asked: Any) -> str:
    """The SHA-256 of what the request asks: its method, path and query, and `asked`, what its
    route read of the body, as values JSON holds."""
    described = json.dumps(
        [request.method, request.url.path, sorted(request.query_params.multi_items()), asked],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(described.encode()).hexdigest()


class Once:
    """A request's Idempotency-Key, if it carried one, for the API key that sent it."""

    def __init__(self, session: Session, api_key_id: int | None, key: str | None) -> None:
        self.session = session
        self.api_key_id = api_key_id
        self.key = key if api_key_id is not None else None

    def run[T: BaseModel](
        self, asked: Callable[[], str], status_code: int, work: Callable[[], T]
    ) -> T | JSONResponse:
        """Carry out `work`, whose answer is given with `status_code`, once for the request whose
        fingerprint `asked` gives: a request without a key is carried out as it comes, its
        fingerprint never taken, and one sent again with its key is given the answer kept for
        it."""
        if self.key is None:
            return work()
        claimed = self._claim(asked())
        if isinstance(claimed, JSONResponse):
            return claimed
        try:
            answer = work()
        except BaseException:
            self._release(claimed)
            raise
        self.session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == claimed)
            .values(status_code=status_code, body=answer.model_dump(mode="json"))
        )
        self.session.commit()
        return answer

    def _claim(self, asked: str) -> int | JSONResponse:
        """Claim the key for this request and return the claim's id, or the answer kept for the
        request it was claimed for before. A claim given back or removed between the insert and
        the look is claimed again; one that keeps vanishing is in flight as far as this knows."""
        for _ in range(3):
            claimed = self.session.scalar(
                insert(IdempotencyKey)
                .values(api_key_id=self.api_key_id, key=self.key, fingerprint=asked)
                .on_conflict_do_nothing(index_elements=["api_key_id", "key"])
                .returning(IdempotencyKey.id)
            )
            if claimed is not None:
                self.session.commit()
                return claimed
            # The lock orders two requests that both find an abandoned claim: the second waits
            # for the first to take it over, and then finds it in flight.
            row = self.session.scalar(
                select(IdempotencyKey)
                .where(IdempotencyKey.api_key_id == self.api_key_id, IdempotencyKey.key == self.key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                continue
            if row.fingerprint != asked:
                self.session.commit()
                raise HTTPException(422, KEY_REUSED)
            if row.status_code is not None:
                answer = JSONResponse(row.body, status_code=row.status_code)
                answer.headers[REPLAYED] = "true"
                self.session.commit()
                return answer
            if datetime.now(UTC) - row.created_at < ABANDONED:
                self.session.commit()
                break
            row.created_at = datetime.now(UTC)
            self.session.commit()
            return row.id
        raise HTTPException(409, IN_FLIGHT, headers={"Retry-After": str(IN_FLIGHT_RETRY)})

    def _release(self, claimed: int) -> None:
        """Give the key back after its request was refused or failed, for a second try to carry
        out. A database that cannot be reached keeps the claim, which the next request with the
        key takes over once it is ABANDONED."""
        try:
            self.session.rollback()
            self.session.execute(delete(IdempotencyKey).where(IdempotencyKey.id == claimed))
            self.session.commit()
        except SQLAlchemyError:
            log.warning(
                "The claim %s of an idempotency key could not be given back after its request "
                "failed; the key's next request takes it over once it is abandoned",
                claimed,
            )


def prune_idempotency_keys(connection: Connection, age: timedelta = KEPT) -> int:
    """Remove the keys claimed longer ago than `age`; returns how many."""
    return connection.execute(_PRUNE, {"before": datetime.now(UTC) - age}).rowcount
