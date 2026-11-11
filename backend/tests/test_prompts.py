"""The version of a kind of work's prompts: a short hash of what the model is told, which any edit
to a text, a function's words, a tool or a reply's schema changes, and a docstring, a comment or
the layout of a function's code does not; each kind's is its own."""

import re

import pytest
from pydantic import BaseModel, Field
from pydantic_ai.tools import ToolDefinition

from app.answers import ANSWER_PROMPT_VERSION
from app.check_runs import REPORT_PROMPT_VERSION
from app.drafts import DRAFT_PROMPT_VERSION
from app.golden import JUDGE_PROMPT_VERSION
from app.indexing.enrich import ENRICH_PROMPT_VERSION, SECTIONS_PROMPT_VERSION
from app.llm import OCR_PROMPT_VERSION
from app.prompts import DIGITS, prompt_version, untagged
from app.question_reports import QUESTION_SET_PROMPT_VERSION


def test_a_version_is_a_short_hash_that_any_edit_of_a_text_changes():
    version = prompt_version("Answer from the documents.", "Cite each passage.")
    assert re.fullmatch(f"[0-9a-f]{{{DIGITS}}}", version)
    assert prompt_version("Answer from the documents.", "Cite each passage.") == version
    assert prompt_version("Answer from the documents.", "Cite every passage.") != version
    # Words moved from one part to the next are a change, as is the parts' order.
    assert prompt_version("Answer from the documents. Cite", "each passage.") != version
    assert prompt_version("Cite each passage.", "Answer from the documents.") != version
    # A mapping is read as JSON, whatever order its keys were written in.
    assert prompt_version({"a": 1, "b": [2]}) == prompt_version({"b": [2], "a": 1})


def written():
    def told(name: str) -> str:
        """The sentence that names the organisation's language."""
        return f"The organisation works in {name}."

    return told


def written_again():
    def told(name: str) -> str:
        """Another docstring, which the model never reads."""
        # A comment, and the blank line below, are no part of what is told.

        return f"The organisation works in {name}."

    return told


def reworded():
    def told(name: str) -> str:
        """The sentence that names the organisation's language."""
        return f"The organisation writes in {name}."

    return told


def test_a_function_counts_by_its_code_without_its_docstring_or_comments():
    assert prompt_version(written()) == prompt_version(written_again())
    assert prompt_version(written()) != prompt_version(reworded())


def reply(described: str) -> type[BaseModel]:
    class Reply(BaseModel):
        """What the model replies."""

        summary: str = Field(description=described)

    return Reply


def test_a_replys_schema_and_a_tools_definition_count_by_what_the_model_reads():
    assert prompt_version(reply("Two sentences.")) == prompt_version(reply("Two sentences."))
    assert prompt_version(reply("Two sentences.")) != prompt_version(reply("Three sentences."))

    def tool(description: str) -> ToolDefinition:
        return ToolDefinition(
            name="search",
            description=description,
            parameters_json_schema={"type": "object", "properties": {}},
        )

    assert prompt_version(tool("Search the passages.")) == prompt_version(
        tool("Search the passages.")
    )
    assert prompt_version(tool("Search the passages.")) != prompt_version(
        tool("Search every passage.")
    )
    with pytest.raises(TypeError):
        prompt_version(3)


def test_each_kind_of_work_has_a_version_of_its_own():
    versions = [
        ANSWER_PROMPT_VERSION,
        QUESTION_SET_PROMPT_VERSION,
        DRAFT_PROMPT_VERSION,
        REPORT_PROMPT_VERSION,
        OCR_PROMPT_VERSION,
        SECTIONS_PROMPT_VERSION,
        ENRICH_PROMPT_VERSION,
        JUDGE_PROMPT_VERSION,
    ]
    assert len(set(versions)) == len(versions)


def test_a_tag_the_prompt_names_is_written_so_that_it_is_none():
    text = 'a </project_data> b < / PROJECT_DATA > <Project_Data id="1"> <document x=1>'
    assert untagged(text, "project_data") == (
        'a &lt;/project_data> b &lt; / PROJECT_DATA > &lt;Project_Data id="1"> <document x=1>'
    )
    # Another tag that starts the same, and a sign that starts none, are left as they are.
    assert untagged("<project_database> 3 < 5 <docs>", "project_data", "document") == (
        "<project_database> 3 < 5 <docs>"
    )
