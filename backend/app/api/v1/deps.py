"""What every route of the versioned API (app/api/v1/) shares: the API key it is called with, the
Idempotency-Key a request that makes something may carry (app/idempotency.py), the pages its lists
come in, and the refusals its contract names."""

import base64
import json
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request

from app.api.deps import AccountDep, SessionDep
from app.api.v1.schemas import Error, PlanError
from app.apikeys import CHALLENGE, bearer
from app.idempotency import HEADER, MAX_LENGTH, Once, key_of

NO_KEY = (
    "The versioned API is called with an API key, in an `Authorization: Bearer emk_…` header. An "
    "owner or admin of the organisation makes one on its page."
)
BAD_CURSOR = "This page cannot be found. List again from the first page."

# How many items a page of a list holds unless the request asks for another number.
PAGE = 50
MOST = 200

Limit = Annotated[int, Query(ge=1, le=MOST, description="How many items the page holds.")]
After = Annotated[
    str | None, Query(description="The `next` of the page before; none for the first page.")
]

# The refusals every route may answer with, besides the ones of its own its description names:
# each says in `detail` what to do next, in a sentence for the person the program works for.
ERRORS: dict[int | str, dict] = {
    401: {"model": Error, "description": "No API key, or one revoked, rolled or run out."},
    402: {
        "model": PlanError,
        "description": "Past what the organisation's plan allows; `limit` names the figure.",
    },
    403: {"model": Error, "description": "The key's scope or its member's role does not allow it."},
    404: {"model": Error, "description": "Nothing of this id that the key's member sees."},
    429: {
        "model": Error,
        "description": "Past a rate, or too much at once: send it again after `Retry-After`.",
    },
}


async def require_key(request: Request) -> None:
    """Refuse a request without an API key: the versioned API is for programs, which carry no
    cookie, and so needs no defence against a page elsewhere acting with a member's session.
    Async, as nothing here waits."""
    if bearer(request) is None:
        raise HTTPException(401, NO_KEY, headers=CHALLENGE)


def get_once(
    session: SessionDep,
    account: AccountDep,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=HEADER,
            description=(
                f"A string of the program's own, at most {MAX_LENGTH} characters, such as a "
                "UUID: the request sent again with it within a day is given the first one's "
                "answer, and carried out once."
            ),
        ),
    ] = None,
) -> Once:
    return Once(session, account.key.id if account.key else None, key_of(idempotency_key))


OnceDep = Annotated[Once, Depends(get_once)]


def cursor_of(*values: str | int) -> str:
    """Where the next page starts, as `after` takes it: the values the list is ordered by of the
    last item shown, opaque to the program, so that the order may take more of them later."""
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode().rstrip("=")


def cursor(after: str | None, *kinds: type) -> tuple | None:
    """The values an `after` carries, of the kinds the list is ordered by; None for the first
    page, and anything else refused rather than guessed at."""
    if after is None:
        return None
    try:
        values = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
    except (ValueError, TypeError):
        raise HTTPException(422, BAD_CURSOR) from None
    if (
        not isinstance(values, list)
        or len(values) != len(kinds)
        or not all(type(value) is kind for value, kind in zip(values, kinds, strict=False))
        or any(
            kind is int and not 0 <= value < 2**63
            for value, kind in zip(values, kinds, strict=False)
        )
    ):
        raise HTTPException(422, BAD_CURSOR)
    return tuple(values)


def shown[T](rows: Sequence[T], limit: int) -> tuple[Sequence[T], bool]:
    """The rows of a page read with one more than it holds, and whether more follow."""
    return rows[:limit], len(rows) > limit
