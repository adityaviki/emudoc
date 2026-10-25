"""Who reads what work cost: an organisation's owners and admins, never its members or viewers.

Every usage line the members' pages show (an answer's, a draft's, a report's, a document's:
`UsageOut` in app/schemas.py, and the `done` event an answer's stream ends its run with) names the
model, the calls and the tokens to whoever reads the work, and its cost only to an owner or an
admin (decided 2026-10-25): the cost is the platform's price for the model, a matter for whoever
answers for the organisation's spending.

The server leaves it out rather than the page hiding it, and it decides once a request rather than
in each route: `CostsWithheld` gives every request a reader who may not see costs, the account's
dependency (`get_secured_account` in app/api/deps.py) shows them to an owner's or an admin's
request, and `UsageOut` serialises the cost as null unless `costs_shown()`. So a route that adds a
usage line withholds its cost from members by default, and so does anything serialised outside a
request. The reader is one mutable object a request, as the log's context is (app/logs.py), since
a sync dependency runs in a pool thread with a copy of the request's context, where setting the
variable itself would be lost.
"""

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send


class _Reader:
    """Whether the request's account may read what work cost."""

    costs = False


_reader: ContextVar[_Reader | None] = ContextVar("cost_reader", default=None)


class CostsWithheld:
    """Start every request as a reader who may not see what work cost."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            _reader.set(_Reader())
        await self.app(scope, receive, send)


def show_costs(shown: bool) -> None:
    """Let the rest of the request show what work cost, or not; outside a request, nothing."""
    if (reader := _reader.get()) is not None:
        reader.costs = shown


def costs_shown() -> bool:
    """Whether what the request answers may hold what work cost."""
    reader = _reader.get()
    return reader is not None and reader.costs
