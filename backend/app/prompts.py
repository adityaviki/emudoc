"""The version of the prompts a piece of model work was asked with, so that the quality program
(3.14) can tell a golden run, a fact-accuracy run or a month of costs before a change to the
prompts from one after it.

A version is the first eight hex digits of a SHA-256 over what the app tells the model to do for
one kind of work: its instructions (the system prompt, the task said after it, a request's own
words), the tools it is offered, by name, description and input schema, and the schema its reply
is held to. Nothing is bumped by hand: an edit to any of them gives a new version, so a version
that stayed means none was edited. What the model is given to work on is in none, since it varies
by run: the documents, the member's words, a tool's result, the input a draft or a report is
written from, a template's fields, the project's name and the date. Nor are the model's settings,
nor what Pydantic AI writes around a request, which a release of it may change under one version.

Each module that asks the model works out its kind's version beside its prompts, once, as it is
imported (`ANSWER_PROMPT_VERSION` in app/answers.py, `ENRICH_PROMPT_VERSION` in
app/indexing/enrich.py, and the others named in its parts' comment). A part is a text, a mapping
read as JSON, a Pydantic model (its JSON schema, with its docstring and its fields' descriptions,
which the model reads too), a tool's definition, or a function that writes an instruction around
what varies (`told` in app/scopes.py), counted by its code without its docstring and comments: an
edit to a sentence it holds changes the version as an edit to a constant does, and so does any
other change to its code, which is why only the functions that write instructions are parts. A
Python or Pydantic release that prints code or a schema differently changes every version once.

The usage rows a kind's model calls are recorded in carry its version (`prompt_version` in
app/usage.py), and the usage an answer, a document, a run or a draft shows keeps the latest by
kind; the `costs` command splits each kind's cost by version (`prompt_costs`), and the `golden`
and `fact-accuracy` reports name the versions they ran.

What the model works on is shown inside tags the prompt names (`<document>`, the
agent's `<project_data>`), which the prompt says hold data and never instructions. `untagged`
writes a tag of those inside the words shown so that it is none, since a document that wrote
`</document>` would otherwise end what is shown and go on as the prompt.
"""

import ast
import hashlib
import inspect
import json
import re
import textwrap
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel
from pydantic_ai.tools import ToolDefinition

# Eight hex digits tell a kind's versions apart for as long as anyone compares them.
DIGITS = 8


def prompt_version(
    *parts: str | Mapping[str, Any] | type[BaseModel] | ToolDefinition | Callable[..., Any],
) -> str:
    """The version of the prompts made of `parts`, in their order."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(_text_of(part).encode())
        # Each part's end, so that words moved from one part to the next are a change.
        digest.update(b"\0")
    return digest.hexdigest()[:DIGITS]


def _text_of(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, ToolDefinition):
        return _json(
            {
                "name": part.name,
                "description": part.description,
                "parameters": part.parameters_json_schema,
            }
        )
    if isinstance(part, Mapping):
        return _json(part)
    # Before any callable: a class is one.
    if isinstance(part, type) and issubclass(part, BaseModel):
        return _json(part.model_json_schema())
    if callable(part):
        return _code(part)
    raise TypeError(f"A prompt's version is not made of a {type(part).__name__}.")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _code(function: Callable[..., Any]) -> str:
    """The function's code as Python prints it back from its tree, without its docstring, its
    comments or its layout."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = tree.body[0]
    if isinstance(definition, ast.FunctionDef) and ast.get_docstring(definition) is not None:
        definition.body = definition.body[1:] or [ast.Pass()]
    return ast.unparse(definition)


def untagged(text: str, *tags: str) -> str:
    """`text` with each opening or closing tag of `tags` in it, in any case and spacing, written
    so that it is no tag: its `<` as `&lt;`."""
    names = "|".join(re.escape(tag) for tag in tags)
    return re.sub(rf"<(?=\s*/?\s*(?:{names})\b)", "&lt;", text, flags=re.IGNORECASE)
