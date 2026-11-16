"""The versioned API, under /api/v1: what programs build on, which changes by addition alone.

The page's routes under /api change with the page; these are a contract (app/api/v1/contract.py
holds them to the one published, `openapi.json` beside this file). Each route acts as the member
an API key acts as (app/apikeys.py) and is called with one alone, `require_key` refusing a
request without; within that, what may be seen and changed, the rates and the audit log are
the page's, since each change goes through the page's own route. What a route answers
is the API's own model (app/api/v1/schemas.py), never the page's, so a field the page renames
stays as it was here; a list comes a page at a time, `next` naming where the next starts; a
request that makes something may carry an Idempotency-Key (app/idempotency.py); and a refusal is
`detail`, a sentence, as the page's are.

The operations are named by their functions (`operation_id`), which are unique across the
modules, so that a client generated from the schema keeps its names.
"""

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from app.api.v1 import chats, library, query, runs, status
from app.api.v1.deps import ERRORS, require_key


def operation_id(route: APIRoute) -> str:
    return route.name


ROUTERS = (
    status.router,
    library.router,
    query.router,
    chats.router,
    runs.router,
)

router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_key)],
    responses=ERRORS,
    generate_unique_id_function=operation_id,
)
for part in ROUTERS:
    router.include_router(part)
