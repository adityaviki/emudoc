"""The golden questions (app/golden.py): their known answers are what the sample's documents say,
an answer is graded by its words and its citations, and a run asks each question of the agent
over a copy of the sample that it deletes after, recording nothing."""

import csv
import re
from decimal import Decimal

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from sqlalchemy import func, select

from app import maintenance
from app.config import get_settings
from app.db import SessionLocal
from app.drafts import CITATION
from app.golden import (
    CONTRACT,
    GOLDEN,
    INVOICES,
    NO,
    PRICE_LIST,
    YES,
    Golden,
    describe_golden,
    grade,
    holds,
    plain,
    run_golden,
)
from app.models import Document, Organization, Passage, Project, UsageEvent
from app.sample import FILES, NAME, QUESTIONS, sample_files
from tests.conftest import FakeEmbedder, FakeLLM, FakeTurn

# The figures a right answer works out from what the documents say, rather than reads in them.
WORKED_OUT = {"151,528.65", "2028"}
# What the grading says of an answer that says neither that something is not so nor that the
# documents do not say.
SAYS_NO_NO = f'does not say {", ".join(f'"{way}"' for way in NO[:-1])} or "{NO[-1]}"'


def text_of(name: str) -> str:
    """A sample document's passages as the grading reads an answer."""
    (file,) = [file for file in sample_files() if file.name == name]
    return plain(" ".join(passage["text"] for passage in file.index["passages"]))


def figures(pattern: str, name: str) -> list[tuple[str, ...]]:
    """What a pattern finds in a document's passages as they are written."""
    (file,) = [file for file in sample_files() if file.name == name]
    return re.findall(pattern, " ".join(passage["text"] for passage in file.index["passages"]))


class Reader(FakeLLM):
    """A model that reads the documents a golden question cites and answers with the first way
    of each thing a right answer says, citing the first passage of each; or answers `wrong`'s
    words for a question it names, citing nothing it read."""

    def __init__(self, wrong: dict[str, str] | None = None):
        super().__init__()
        self.wrong = wrong or {}

    def stream_agent(self, instructions, messages, tools):
        self.agent_prompts.append(instructions)
        self.agent_tools.append([tool.name for tool in tools])
        question = messages[0].parts[0].content
        (golden,) = [golden for golden in GOLDEN if golden.question == question]
        if len(messages) == 1:
            with SessionLocal() as session:
                ids = session.scalars(
                    select(Document.id)
                    .join(Project)
                    .where(Project.name == NAME, Document.filename.in_(golden.cites))
                ).all()
            reads = [ToolCallPart("read", {"document_id": id_}) for id_ in ids]
            return FakeTurn(*reads or [ToolCallPart("search", {"query": question})])
        if question in self.wrong:
            return FakeTurn(TextPart(self.wrong[question]))
        shown = [
            found.group(0)
            for part in messages[-1].parts
            if isinstance(part, ToolReturnPart) and (found := CITATION.search(part.content))
        ]
        said = " ".join(ways[0] for ways in golden.says)
        return FakeTurn(TextPart(f"{said} {' '.join(shown)}"))


def test_the_known_answers_are_what_the_documents_say():
    names = {file.name for file in sample_files()}
    for golden in GOLDEN:
        assert set(golden.cites) <= names, golden.question
        for ways in golden.says:
            if ways in (NO, YES) or set(ways) & WORKED_OUT:
                continue
            assert any(holds(text_of(name), way) for name in golden.cites for way in ways), (
                golden.question,
                ways,
            )
    # The three invoices' totals due, summed.
    totals = [
        Decimal(total.replace(",", ""))
        for name in INVOICES
        for (total,) in [figures(r"Total due \(EUR\) \| ([\d,.]+)", name)]
    ]
    assert sum(totals) == Decimal("151528.65")
    # Twenty-four months from 1 March 2026.
    assert holds(text_of(CONTRACT), "1 March 2026") and holds(text_of(CONTRACT), "24 months")
    # Invoice INV-2026-0503 bills 340 pallets, which the contract's 250 and more discounts by
    # 6 %, and grants no discount at all.
    pallets = figures(r"\(pallet\) \| (\d+) \|", INVOICES[2])
    assert sum(int(count) for count in pallets) == 340
    assert holds(text_of(CONTRACT), "250+ | 6%")
    assert "discount" not in text_of(INVOICES[2])
    # Every unit price of every invoice is the price list's.
    with (FILES / PRICE_LIST).open() as listed:
        prices = {row["description"]: Decimal(row["price_eur"]) for row in csv.DictReader(listed)}
    for name in INVOICES:
        lines = figures(r"\| ([\w ]+?) \((?:pallet|roll box|box)\) \| \d+ \| ([\d.]+) \|", name)
        assert lines and all(prices[item] == Decimal(price) for item, price in lines), name
    # Nothing of insurance anywhere, so the answer to the last question is that there is none.
    assert not any("insur" in text_of(name) for name in names)
    # The questions an empty chat of the sample offers are among them, as members ask them.
    assert set(QUESTIONS) <= {golden.question for golden in GOLDEN}


def test_an_answer_is_graded_by_its_words_and_its_citations():
    golden = Golden("What is due?", (("43,476.65",), NO), (INVOICES[1],))
    right = "EUR 43\u202f476.65, and it doesn\u2019t carry a discount [P3]."
    assert grade(golden, right, {INVOICES[1]}, []) == []
    assert grade(golden, "EUR 143,476.65, nothing more [P9].", set(), [9]) == [
        'does not say "43,476.65"',
        f"cites no passage of {INVOICES[1]}",
        "cites P9, which no tool showed it",
    ]
    assert grade(golden, "It is due.", {INVOICES[1]}, []) == [
        'does not say "43,476.65"',
        SAYS_NO_NO,
    ]
    # Whole words and figures: "6%" is not in "16%", nor "no" in "note".
    assert holds(plain("a 6 % discount"), "6%") and not holds(plain("VAT at 16%"), "6%")
    assert not holds(plain("See the note."), "no")


def test_a_run_asks_each_question_over_a_copy_of_the_sample_and_leaves_nothing(session):
    reader = Reader()
    run = run_golden(get_settings(), reader, FakeEmbedder())

    assert [asked.golden for asked in run.asked] == list(GOLDEN)
    assert run.failed == [] and run.by_words is None
    # Asked as a member's answer is, in the sample's own project.
    assert all(f"Project: {NAME}" in prompt for prompt in reader.agent_prompts)
    assert all("suggest_follow_ups" in offered for offered in reader.agent_tools)
    assert all(asked.usage.calls == 2 for asked in run.asked)
    # The organisation goes with everything the run made and recorded: the embed stage's usage
    # among it.
    for table in (Organization, Project, Passage, UsageEvent):
        assert session.scalar(select(func.count()).select_from(table)) == 0
    assert describe_golden(run) == [
        *[f"  ok      {asked.golden.question} ({asked.seconds:.1f} s)" for asked in run.asked],
        f"Answered by {get_settings().agent_model}: 26 calls, 260 tokens in, 130 out.",
        f"Every golden question answered as known ({len(GOLDEN)} asked).",
    ]


def test_the_command_prints_a_wrong_answer_whole_and_exits_1(session, monkeypatch, capsys):
    wrong = {QUESTIONS[0]: "It does, at 3 % [P999999].\nThe contract allows it."}
    down = FakeEmbedder()
    down.fail = True
    monkeypatch.setattr(
        maintenance, "run_golden", lambda settings: run_golden(settings, Reader(wrong), down)
    )
    with pytest.raises(SystemExit) as stop:
        maintenance.main(["golden"])

    assert stop.value.code == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"  FAILED  {QUESTIONS[0]} (")
    assert out[1] == (
        '          does not say "6%", "6 per cent", "6 percent", "six per cent" or "six percent"; '
        f'does not say "340" or "250"; {SAYS_NO_NO}; cites no passage of {INVOICES[2]}; '
        f"cites no passage of {CONTRACT}; cites P999999, which no tool showed it"
    )
    assert out[2:4] == [
        "          | It does, at 3 % [P999999].",
        "          | The contract allows it.",
    ]
    assert out[4].startswith(f"  ok      {QUESTIONS[1]} (")
    assert out[-3] == (
        "The search matched words alone, since the passages could not be embedded: "
        f"{FakeEmbedder.UNREACHABLE}"
    )
    assert out[-1] == f"{len(GOLDEN) - 1} of {len(GOLDEN)} golden questions answered as known."
    assert session.scalar(select(func.count()).select_from(Organization)) == 0


def test_a_run_without_a_model_is_refused(session):
    reader = Reader()
    reader.available = False
    with pytest.raises(RuntimeError, match="answered by a model, and none answers"):
        run_golden(get_settings(), reader, FakeEmbedder())
    assert session.scalar(select(func.count()).select_from(Organization)) == 0
