"""The versioned API's contract: its OpenAPI document, as the routes make it (`contract`), and the
copy published beside them (`openapi.json`, PUBLISHED), which programs and their generated
clients are written against.

The document lists the events posted to an organisation's webhooks beside the routes (OpenAPI's
`webhooks`, app/api/v1/webhooks.py), whose bodies a program reads as it reads an answer.

The two are held equal by tests/test_api_v1.py, so that no change to the API goes out unnoticed;
and a change may only add (`breaking_changes`): a new route or event, a field more in an answer
or an event's body, a new parameter or field a request may leave out. Anything that would stop a
program written against the published copy (an operation, an event, a parameter, an event's
header, an answer's status or field gone; a field's type or values changed; a field an answer or
an event always had now sometimes missing; something a request must now send) goes into a
/api/v2 instead. Written again from the routes, refusing a change that
breaks, with:

    uv --directory emudoc/backend run python -m app.api.v1.contract

Before 1.0 a feature taken out of the app takes its routes, fields and events out of /api/v1 too
(S.6, decided by the owner on 2026-11-16), a break the changelog says under its Admins: `--breaking`
writes the contract all the same, listing what breaks.
"""

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.api import v1
from app.api.v1.webhooks import events

PUBLISHED = Path(__file__).with_name("openapi.json")
TITLE = "Emudoc API"
VERSION = "1"
DESCRIPTION = (
    "Programs read and change an organisation's projects with an API key, as the member who "
    "made it. Lists come a page at a time, `next` naming where the next starts; a request that "
    "makes something may carry an Idempotency-Key; a refusal says what to do next in `detail`."
)
# The groups the operations and events are listed in (each route's `tags`), in the reference's
# order, each with what it holds; `x-displayName` is a group's heading, as Redoc reads it too.
TAGS = [
    {
        "name": "status",
        "x-displayName": "Status",
        "description": "What the deployment can do just now, and what one upload may carry.",
    },
    {
        "name": "projects",
        "x-displayName": "Projects",
        "description": (
            "The organisation's projects that the key's member sees, each with documents that "
            "are searched and asked about together."
        ),
    },
    {
        "name": "folders",
        "x-displayName": "Folders",
        "description": "The folders a project's library keeps its documents in.",
    },
    {
        "name": "documents",
        "x-displayName": "Documents",
        "description": (
            "Files uploaded into a project, each indexed in the background before it is "
            "searched and asked about."
        ),
    },
    {
        "name": "facts",
        "x-displayName": "Facts",
        "description": (
            "The values read from documents by the project's extraction templates, each with "
            "the words it was read from."
        ),
    },
    {
        "name": "search",
        "x-displayName": "Search",
        "description": "The passages of a project's documents, found by keywords and by meaning.",
    },
    {
        "name": "chats",
        "x-displayName": "Chats",
        "description": (
            "Questions asked about a project's documents, and the answers the agent writes, "
            "followed as they are written."
        ),
    },
    {
        "name": "webhooks",
        "x-displayName": "Webhooks",
        "description": (
            "The events posted to the organisation's webhooks as they happen, which owners and "
            "admins add on its page."
        ),
    },
]
# What a schema says about itself rather than about the values it takes, which may change freely.
_WORDS = frozenset({"title", "description", "examples"})


def contract() -> dict[str, Any]:
    """The OpenAPI document of the versioned API's routes and webhooks' events, alone."""
    app = FastAPI(title=TITLE, version=VERSION, description=DESCRIPTION, openapi_tags=TAGS)
    app.include_router(v1.router, prefix="/api")
    app.webhooks.include_router(events, generate_unique_id_function=v1.operation_id)
    document = app.openapi()
    # FastAPI words an event's answer as a route's, a JSON body and a 422 of its own checks;
    # Emudoc reads a receiver's status alone, any 2xx.
    for operations in document.get("webhooks", {}).values():
        for operation in operations.values():
            taken = operation["responses"]["200"]["description"]
            operation["responses"] = {"2XX": {"description": taken}}
    return document


def published() -> dict[str, Any]:
    return json.loads(PUBLISHED.read_text())


def breaking_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What `after` changes of `before` that a program written against `before` would trip on,
    each in a sentence; empty when it only adds."""
    problems: list[str] = []
    for path, operations in before["paths"].items():
        for method, operation in operations.items():
            name = f"{method.upper()} {path}"
            now = after["paths"].get(path, {}).get(method)
            if now is None:
                problems.append(f"{name} is gone.")
                continue
            problems += _parameters(name, operation, now)
            problems += _body(name, operation, now)
            problems += _answers(name, operation, now)
    for event, operations in before.get("webhooks", {}).items():
        for method, operation in operations.items():
            now = after.get("webhooks", {}).get(event, {}).get(method)
            if now is None:
                problems.append(f"The event {event} is no longer sent.")
                continue
            problems += _sent(event, operation, now)
    schemas = after.get("components", {}).get("schemas", {})
    inputs, outputs = _uses(before)
    for name, schema in before.get("components", {}).get("schemas", {}).items():
        now = schemas.get(name)
        if now is None:
            problems.append(f"The schema {name} is gone.")
            continue
        problems += _fields(name, schema, now, name in inputs, name in outputs)
    return problems


def _parameters(name: str, before: dict, after: dict) -> list[str]:
    was = {(p["in"], p["name"]): p for p in before.get("parameters", [])}
    now = {(p["in"], p["name"]): p for p in after.get("parameters", [])}
    problems = []
    for (where, called), parameter in was.items():
        if (where, called) not in now:
            problems.append(f"{name} no longer takes the {where} parameter {called}.")
        elif _bare(now[(where, called)].get("schema")) != _bare(parameter.get("schema")):
            problems.append(f"{name} takes the {where} parameter {called} as something else.")
        elif now[(where, called)].get("required") and not parameter.get("required"):
            problems.append(f"{name} now requires the {where} parameter {called}.")
    for (where, called), parameter in now.items():
        if (where, called) not in was and parameter.get("required"):
            problems.append(f"{name} now requires the {where} parameter {called}.")
    return problems


def _body(name: str, before: dict, after: dict) -> list[str]:
    was, now = before.get("requestBody"), after.get("requestBody")
    if was is None:
        return [f"{name} now requires a body."] if now and now.get("required") else []
    if now is None:
        return [f"{name} no longer takes a body."]
    problems = []
    for media, content in was.get("content", {}).items():
        if media not in now.get("content", {}):
            problems.append(f"{name} no longer takes {media}.")
        elif _bare(now["content"][media].get("schema")) != _bare(content.get("schema")):
            problems.append(f"{name} takes another body as {media}.")
    if now.get("required") and not was.get("required"):
        problems.append(f"{name} now requires a body.")
    return problems


def _answers(name: str, before: dict, after: dict) -> list[str]:
    problems = []
    for status, answer in before.get("responses", {}).items():
        given = after.get("responses", {}).get(status)
        if given is None:
            problems.append(f"{name} no longer answers {status}.")
            continue
        for media, content in answer.get("content", {}).items():
            if media not in given.get("content", {}):
                problems.append(f"{name} no longer answers {status} as {media}.")
            elif _bare(given["content"][media].get("schema")) != _bare(content.get("schema")):
                problems.append(f"{name} answers {status} with something else as {media}.")
    return problems


def _sent(event: str, before: dict, after: dict) -> list[str]:
    """An event's headers and body, which a program's own route reads: none gone or changed."""
    was = {(p["in"], p["name"]): p for p in before.get("parameters", [])}
    now = {(p["in"], p["name"]): p for p in after.get("parameters", [])}
    problems = []
    for (where, called), parameter in was.items():
        if (where, called) not in now:
            problems.append(f"The event {event} no longer carries the {where} {called}.")
        elif _bare(now[(where, called)].get("schema")) != _bare(parameter.get("schema")):
            problems.append(f"The event {event} carries the {where} {called} as something else.")
    for media, content in before.get("requestBody", {}).get("content", {}).items():
        given = after.get("requestBody", {}).get("content", {})
        if media not in given:
            problems.append(f"The event {event} is no longer sent as {media}.")
        elif _bare(given[media].get("schema")) != _bare(content.get("schema")):
            problems.append(f"The event {event} is sent with something else as {media}.")
    return problems


def _fields(name: str, before: dict, after: dict, taken: bool, given: bool) -> list[str]:
    """A schema's fields: none gone or changed; one that every answer had is in every answer
    still, and a request is asked for no field it was not asked for before."""
    problems = []
    fields = after.get("properties", {})
    for field, schema in before.get("properties", {}).items():
        if field not in fields:
            problems.append(f"{name}.{field} is gone.")
        elif _bare(fields[field]) != _bare(schema):
            problems.append(f"{name}.{field} takes other values.")
    was, now = set(before.get("required", [])), set(after.get("required", []))
    if given and (dropped := was - now):
        problems.append(f"{name} no longer always holds {', '.join(sorted(dropped))}.")
    if taken and (added := now - was):
        problems.append(f"{name} now requires {', '.join(sorted(added))}.")
    if _bare({k: v for k, v in before.items() if k not in ("properties", "required")}) != _bare(
        {k: v for k, v in after.items() if k not in ("properties", "required")}
    ):
        problems.append(f"The schema {name} takes other values.")
    return problems


def _uses(document: dict) -> tuple[set[str], set[str]]:
    """The schemas a request's parameters and body name, and those an answer or an event names,
    each with the schemas they name in turn."""
    schemas = document.get("components", {}).get("schemas", {})
    taken: list[Any] = []
    given: list[Any] = []
    for operations in document["paths"].values():
        for operation in operations.values():
            taken += [p.get("schema") for p in operation.get("parameters", [])]
            taken.append(operation.get("requestBody"))
            given += list(operation.get("responses", {}).values())
    for operations in document.get("webhooks", {}).values():
        for operation in operations.values():
            given += [p.get("schema") for p in operation.get("parameters", [])]
            given.append(operation.get("requestBody"))
    return _reached(taken, schemas), _reached(given, schemas)


def _reached(roots: list[Any], schemas: dict) -> set[str]:
    found: set[str] = set()
    pending = list(roots)
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                called = ref.removeprefix("#/components/schemas/")
                if called not in found:
                    found.add(called)
                    pending.append(schemas.get(called))
            pending += list(node.values())
        elif isinstance(node, list):
            pending += node
    return found


def _bare(schema: Any) -> Any:
    """A schema without the words about it: what values it takes."""
    if isinstance(schema, dict):
        return {key: _bare(value) for key, value in schema.items() if key not in _WORDS}
    if isinstance(schema, list):
        return [_bare(value) for value in schema]
    return schema


def write(breaking: bool = False) -> int:
    """Write the contract again from the routes, unless the change would break a program
    written against the one published and `breaking` does not say that it may; returns the exit
    status."""
    made = contract()
    problems = breaking_changes(published(), made) if PUBLISHED.exists() else []
    if problems and not breaking:
        print("The routes break the published contract, so it was not written:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        print("Keep these as they were, and put the change into /api/v2.", file=sys.stderr)
        return 1
    if problems:
        print("The contract breaks programs written against the published one:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        print("Say each under Admins in CHANGELOG.md's Unreleased.", file=sys.stderr)
    PUBLISHED.write_text(json.dumps(made, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {PUBLISHED}.")
    return 0


if __name__ == "__main__":
    sys.exit(write(breaking="--breaking" in sys.argv[1:]))
