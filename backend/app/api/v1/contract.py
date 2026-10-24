"""The versioned API's contract: its OpenAPI document, as the routes make it (`contract`), and the
copy published beside them (`openapi.json`, PUBLISHED), which programs and their generated
clients are written against.

The two are held equal by tests/test_api_v1.py, so that no change to the API goes out unnoticed;
and a change may only add (`breaking_changes`): a new route, a field more in an answer, a new
parameter or field a request may leave out. Anything that would stop a program written against
the published copy (an operation, a parameter, an answer's status or field gone; a field's type
or values changed; a field an answer always had now sometimes missing; something a request must
now send) goes into a /api/v2 instead. Written again from the routes, refusing a change that
breaks, with:

    uv --directory emudoc/backend run python -m app.api.v1.contract
"""

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.api import v1

PUBLISHED = Path(__file__).with_name("openapi.json")
TITLE = "Emudoc API"
VERSION = "1"
DESCRIPTION = (
    "Programs read and change an organisation's projects with an API key, as the member who "
    "made it. Lists come a page at a time, `next` naming where the next starts; a request that "
    "makes something may carry an Idempotency-Key; a refusal says what to do next in `detail`."
)
# What a schema says about itself rather than about the values it takes, which may change freely.
_WORDS = frozenset({"title", "description", "examples"})


def contract() -> dict[str, Any]:
    """The OpenAPI document of the versioned API's routes, alone."""
    app = FastAPI(title=TITLE, version=VERSION, description=DESCRIPTION)
    app.include_router(v1.router, prefix="/api")
    return app.openapi()


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
    """The schemas a request's parameters and body name, and those an answer names, each with
    the schemas they name in turn."""
    schemas = document.get("components", {}).get("schemas", {})
    taken: list[Any] = []
    given: list[Any] = []
    for operations in document["paths"].values():
        for operation in operations.values():
            taken += [p.get("schema") for p in operation.get("parameters", [])]
            taken.append(operation.get("requestBody"))
            given += list(operation.get("responses", {}).values())
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


def write() -> int:
    """Write the contract again from the routes, unless the change would break a program
    written against the one published; returns the exit status."""
    made = contract()
    if PUBLISHED.exists() and (problems := breaking_changes(published(), made)):
        print("The routes break the published contract, so it was not written:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        print("Keep these as they were, and put the change into /api/v2.", file=sys.stderr)
        return 1
    PUBLISHED.write_text(json.dumps(made, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {PUBLISHED}.")
    return 0


if __name__ == "__main__":
    sys.exit(write())
