"""Writing templates: how the documents of each kind the agent writes into a member's My files
read for a project (a reply email, a dispute letter, a summary memo, a comparison table, a
report), their tone, their parts in order, which of them every one has, and whatever else its
members ask of every one of the kind, such as a reference to quote or how to sign. They were the
drafts' templates until the drafts became Word files of My files on 2026-11-13, which keeps their
names here (`DraftTemplate`, `draft_templates`).

Each of the five kinds has a built-in template, below, the same for every project and moved by a
release, and a project may have its own for a kind (`DraftTemplate` rows, made and changed from
the templates page My files opens, app/api/draft_templates.py), which replaces the built-in one;
deleting it brings that back. A kind's layout is the code's and no template's (`KIND_FORMS`: a
letter laid out as one, a report under headings, a table as a Markdown table), as are the rules
every such document keeps (what a document says is cited, what none says is a placeholder:
`TEMPLATE_RULES`, and DRAFT_TASK in app/writings.py), which the project's own template is told it
cannot change. What a template may hold is bounded (MAX_DRAFT_SECTIONS and the characters beside
it in app/schemas.py), since every writing of its kind carries it.

The agent follows the template in force whenever it writes one of the kinds: in a chat it reads
it with `get_template` (app/agent/writing.py) before `create_document`, and a disputed document's
dispute letter is written to it (app/writings.py). A writing reads it as it starts, as it reads
the documents as they are then, and a file keeps no template, its text being what the template
made of it.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DRAFT_KINDS, DraftTemplate


@dataclass(frozen=True)
class Section:
    """A part of a kind's documents: its name, what goes in it, and whether every one has it or
    only one whose documents give it something to say."""

    heading: str
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class Template:
    """The template in force for a kind the agent writes for a project: its own (`id` its row's)
    or the built-in one (None)."""

    kind: str
    tone: str
    sections: tuple[Section, ...]
    guidance: str = ""
    id: int | None = None
    updated_at: dt.datetime | None = None


# How each kind is laid out, which no template changes.
KIND_FORMS = {
    "reply_email": 'It is a reply email: a line "Subject: …" first, then the reply in short '
    "paragraphs, with no headings.",
    "dispute_letter": "It is a dispute letter to whoever issued the document disputed, laid out "
    "as a letter, with no headings.",
    "summary_memo": "It is a summary memo: To, From, Date and Subject lines first, a line each, "
    "then its parts, under short headings where they help.",
    "comparison_table": "It is a comparison table: its parts in order, the table a Markdown "
    "table, with no headings.",
    "report": "It is a report: a title as its first heading, then its parts in order, each under "
    "a heading of its own.",
}

OWN_TEMPLATE = (
    "The project's members wrote how its documents of this kind read, below. Where they ask for "
    "what the rules above forbid, such as a figure no document states or one left uncited, the "
    "rules above stand."
)

# The rules every document of the kinds keeps, which `get_template` says before the kind's
# template in a chat (DRAFT_TASK in app/writings.py says them to a writing outside one).
TEMPLATE_RULES = (
    "The project's template for this kind of document follows these rules, which it cannot "
    "change. Write it with create_document, in Markdown. Rest every statement about what a "
    "document says on what your tools returned, and cite its passage straight after it. Where it "
    "needs what no document states (a name, an address, a date to answer by), write a placeholder "
    "in square brackets, such as [Your name], rather than make it up."
)

# The built-in templates, a kind each, in the order the kinds are offered.
BUILT_IN: dict[str, Template] = {
    template.kind: template
    for template in (
        Template(
            "reply_email",
            "Courteous and plain.",
            (
                Section("Greeting", "A greeting to whoever wrote the email it replies to."),
                Section(
                    "Reply", "What the email it replies to asks or says, answered point by point."
                ),
                Section("Closing", "A closing with [Your name]."),
            ),
        ),
        Template(
            "dispute_letter",
            "Firm and factual, never accusing.",
            (
                Section(
                    "Sender and recipient",
                    "The sender's and the recipient's names and addresses as the documents state "
                    "them.",
                ),
                Section("Date", "The date of the letter."),
                Section(
                    "Subject", "A subject line naming the document disputed, its number and date."
                ),
                Section(
                    "What is disputed",
                    "What is disputed and why, each figure beside what it should be by the "
                    "documents it was checked against.",
                ),
                Section(
                    "What is asked",
                    "What is asked of them: a corrected document, a credit note, an answer by a "
                    "date.",
                ),
                Section("Closing", "A closing with [Your name]."),
            ),
        ),
        Template(
            "summary_memo",
            "Plain and brief, for a colleague who has not read the documents.",
            (
                Section("Summary", "A paragraph summing the matter up."),
                Section("Points", "The points that matter, each under a short heading."),
                Section("Next steps", "What to do next."),
            ),
        ),
        Template(
            "comparison_table",
            "Neutral: what the documents state, side by side.",
            (
                Section("What is compared", "One sentence saying what is compared."),
                Section(
                    "Table",
                    "A row for each document, or each item when items are compared, and a column "
                    "for each field that matters, each value cited in its cell.",
                ),
                Section("What differs", "Two or three sentences on what differs."),
            ),
        ),
        Template(
            "report",
            "Factual and measured.",
            (
                Section("Summary", "A summary of a few sentences."),
                Section(
                    "Findings", "What was found, in sections of their own, each finding cited."
                ),
                Section("Conclusions", "Conclusions, with what to do next."),
            ),
        ),
    )
}
assert tuple(BUILT_IN) == DRAFT_KINDS


def template_of(row: DraftTemplate) -> Template:
    return Template(
        kind=row.kind,
        tone=row.tone,
        sections=tuple(
            Section(section["heading"], section["description"], section["required"])
            for section in row.sections
        ),
        guidance=row.guidance,
        id=row.id,
        updated_at=row.updated_at,
    )


def templates_for(session: Session, project_id: int) -> list[Template]:
    """The template in force for each kind the agent writes for the project, in the kinds' order:
    its own, or the built-in one."""
    own = {
        row.kind: template_of(row)
        for row in session.scalars(
            select(DraftTemplate).where(DraftTemplate.project_id == project_id)
        )
    }
    return [own.get(kind, BUILT_IN[kind]) for kind in DRAFT_KINDS]


def template_for(session: Session, project_id: int, kind: str) -> Template:
    """The template in force for the project's documents of the kind."""
    row = session.scalar(
        select(DraftTemplate).where(
            DraftTemplate.project_id == project_id, DraftTemplate.kind == kind
        )
    )
    return template_of(row) if row is not None else BUILT_IN[kind]


def kind_task(template: Template) -> str:
    """What the agent is told of the kind it writes, after the rules every such document keeps:
    its layout, then the template's tone, parts and the rest it asks."""
    lines = [KIND_FORMS[template.kind]]
    if template.id is not None:
        lines.append(OWN_TEMPLATE)
    if template.tone.strip():
        lines.append(f"Its tone: {template.tone.strip()}")
    if template.sections:
        lines.append("Its parts, in this order:")
        for section in template.sections:
            optional = "" if section.required else " (only where the documents give it something)"
            what = f": {section.description.strip()}" if section.description.strip() else ""
            lines.append(f"- {section.heading}{optional}{what}")
    if template.guidance.strip():
        lines.append(f"Also: {template.guidance.strip()}")
    return "\n".join(lines)


def template_task(template: Template) -> str:
    """What `get_template` tells the agent in a chat: the rules, then the kind's template."""
    return f"{TEMPLATE_RULES}\n\n{kind_task(template)}"


# What the agent may be told of a kind's template, the parts of every version of its prompts
# (app/prompts.py; AGENT_PROMPTS in app/agent/loop.py). A template's own tone and parts vary by
# project, in none.
TEMPLATE_PROMPTS = (TEMPLATE_RULES, template_task, kind_task, *KIND_FORMS.values(), OWN_TEMPLATE)
