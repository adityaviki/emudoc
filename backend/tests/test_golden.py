"""The golden questions (app/golden.py): their known answers are what the sample's documents say,
an answer is graded by its words and its citations and judged by the indexing model, and a run
asks each question of the agent over a copy of the sample that it deletes after, recording
nothing."""

import csv
import re
from decimal import Decimal

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import RequestUsage
from sqlalchemy import func, select

from app import maintenance
from app.agent.tools import TOOLS
from app.answers import ANSWER_PROMPT_VERSION
from app.config import get_settings
from app.db import SessionLocal
from app.draft_export import CITATION
from app.golden import (
    CONTRACT,
    GOLDEN,
    INVOICES,
    JUDGE_PROMPT_VERSION,
    NO,
    PRICE_LIST,
    YES,
    Asked,
    CitationVerdict,
    Golden,
    GoldenRun,
    Judged,
    Judgement,
    Support,
    cited_sentences,
    describe_golden,
    grade,
    holds,
    judge,
    plain,
    run_golden,
)
from app.llm import LLMError
from app.models import Document, Organization, Passage, Project, UsageEvent
from app.sample import FILES, NAME, QUESTIONS, sample_files
from app.usage import ModelUsage
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


def judged_by(content: str, doubted: bool) -> Judgement:
    """A judge's reply to `content`: the answer right and each citation supported, or,
    `doubted`, the answer wrong and its first citation unsupported."""
    numbers = [int(number) for number in re.findall(r'<citation number="(\d+)">', content)]
    return Judgement(
        reason="It says what the known answer does not." if doubted else "As known.",
        right=not doubted,
        citations=[
            CitationVerdict(
                number=number,
                reason="It says another thing." if doubted and number == 1 else "It says so.",
                supported=not (doubted and number == 1),
            )
            for number in numbers
        ],
    )


class Reader(FakeLLM):
    """A model that reads the documents a golden question cites and answers with the first way
    of each thing a right answer says, citing the first passage of each; or answers `wrong`'s
    words for a question it names, citing nothing it read. As the judge, it finds every answer
    right and every citation supported but for the questions `doubted` names."""

    def __init__(self, wrong: dict[str, str] | None = None, doubted: tuple[str, ...] = ()):
        super().__init__()
        self.wrong = wrong or {}
        self.doubted = doubted
        # What each judging was asked, in the order asked.
        self.judgings: list[str] = []

    def structured(self, content, schema, usage=None):
        if schema is not Judgement:
            return super().structured(content, schema, usage)
        self.judgings.append(content)
        usage.add(get_settings().index_model, RequestUsage(input_tokens=10, output_tokens=5))
        (question,) = re.findall(r"<question>(.*)</question>", content)
        return judged_by(content, question in self.doubted)

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
        # The known answer the judge reads is one the words pass.
        assert grade(golden, golden.known, set(golden.cites), []) == [], golden.question
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
    golden = Golden("What is due?", "EUR 43,476.65.", (("43,476.65",), NO), (INVOICES[1],))
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
    assert all(offered == list(TOOLS) for offered in reader.agent_tools)
    assert all(asked.usage.calls == 2 for asked in run.asked)
    # Each answer judged by the indexing model against its known answer, with where each
    # passage it cites is and its whole text, where a source carries its first 280 characters.
    assert len(reader.judgings) == len(GOLDEN)
    for asked, judging in zip(run.asked, reader.judgings, strict=True):
        assert f"<known_answer>{asked.golden.known}</known_answer>" in judging
        assert asked.judged.right is True and asked.judged.usage.calls == 1
        assert asked.judged.citations
        assert all(support.supported for support in asked.judged.citations)
    given = [
        passage
        for judging in reader.judgings
        for passage in re.findall(
            r"<where>(.*?)</where>\n<passage>(.*?)</passage>", judging, re.DOTALL
        )
    ]
    whole = {
        (passage["context"], passage["text"])
        for file in sample_files()
        for passage in file.index["passages"]
    }
    assert set(given) <= whole and max(len(text) for _, text in given) > 280
    # The organisation goes with everything the run made and recorded: the embed stage's usage
    # among it.
    for table in (Organization, Project, Passage, UsageEvent):
        assert session.scalar(select(func.count()).select_from(table)) == 0
    citations = sum(len(asked.judged.citations) for asked in run.asked)
    assert describe_golden(run) == [
        *[f"  ok      {asked.golden.question} ({asked.seconds:.1f} s)" for asked in run.asked],
        # Which prompts asked and judged, to tell this run from one after they changed.
        f"Prompt versions: answer {ANSWER_PROMPT_VERSION}, judge {JUDGE_PROMPT_VERSION}.",
        f"Answered by {get_settings().agent_model}: 26 calls, 260 tokens in, 130 out.",
        f"Judged by {get_settings().index_model}: 13 calls, 130 tokens in, 65 out.",
        f"The judge found 13 of 13 answers right, and {citations} of {citations} citations "
        "supported by the passage cited (100%).",
        f"Every golden question answered as known ({len(GOLDEN)} asked).",
    ]


def test_the_command_prints_a_wrong_answer_whole_and_exits_1(session, monkeypatch, capsys):
    wrong = {QUESTIONS[0]: "It does, at 3 % [P999999].\nThe contract allows it."}
    down = FakeEmbedder()
    down.fail = True
    # The judge doubts the wrong answer, and one the words pass.
    reader = Reader(wrong, doubted=QUESTIONS[:2])
    monkeypatch.setattr(
        maintenance, "run_golden", lambda settings: run_golden(settings, reader, down)
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
    # A passage no tool showed is the grading's miss alone: the judge reads none.
    assert "<citation" not in reader.judgings[0]
    assert out[2:5] == [
        "          judged wrong: It says what the known answer does not.",
        "          | It does, at 3 % [P999999].",
        "          | The contract allows it.",
    ]
    # An answer the words pass and the judge doubts is printed whole too, with its doubts.
    assert out[5].startswith(f"  ok      {QUESTIONS[1]} (")
    assert out[6] == "          judged wrong: It says what the known answer does not."
    assert re.fullmatch(
        r'          P\d+ does not support "PO-7708 PO-7722 strike \[P\d+\] \[P\d+\]": '
        r"It says another thing\.",
        out[7],
    )
    assert re.fullmatch(r"          \| PO-7708 PO-7722 strike \[P\d+\] \[P\d+\]", out[8])
    assert out[9].startswith(f"  ok      {QUESTIONS[2]} (")
    assert out[-6] == (
        "The search matched words alone, since the passages could not be embedded: "
        f"{FakeEmbedder.UNREACHABLE}"
    )
    assert out[-3] == f"Judged by {get_settings().index_model}: 13 calls, 130 tokens in, 65 out."
    assert re.fullmatch(
        r"The judge found 11 of 13 answers right, and (\d+) of (\d+) citations supported by the "
        r"passage cited \(\d+%\)\.",
        out[-2],
    )
    assert out[-1] == f"{len(GOLDEN) - 1} of {len(GOLDEN)} golden questions answered as known."
    assert session.scalar(select(func.count()).select_from(Organization)) == 0


def test_an_answer_is_judged_against_the_known_one_and_each_passage_it_cites():
    golden = Golden("What is due?", "EUR 43,476.65.", (("43,476.65",),), (INVOICES[1],))
    passages = {
        7: ("Invoice INV-2026-0458 › Totals", "| Total due (EUR) | 43,476.65 |"),
        8: ("Invoice INV-2026-0458 › Terms", "Payment terms: 30 days."),
    }
    answer = (
        "**EUR 43,476.65** is due [P7]. It is payable in 30 days. [P8][P7]\n"
        "- The VAT is 19 % [P99]."
    )
    asked: list[str] = []

    class Judge(FakeLLM):
        def structured(self, content, schema, usage=None):
            asked.append(content)
            usage.add("judge", RequestUsage(input_tokens=10, output_tokens=5))
            return Judgement(
                reason="As known.",
                right=True,
                citations=[
                    CitationVerdict(number=2, reason="It says 30 days.", supported=True),
                    CitationVerdict(number=1, reason="It says the total.", supported=True),
                    CitationVerdict(
                        number=3, reason="It is the total, not the terms.", supported=False
                    ),
                    # A number no citation has is no verdict.
                    CitationVerdict(number=9, reason="?", supported=False),
                ],
            )

    judged = judge(Judge(), golden, answer, passages)

    # Each sentence once for each passage of those shown it cites, numbered in the order
    # written; P99, which no tool showed, is the grading's to fail.
    (content,) = asked
    assert "<question>What is due?</question>" in content
    assert "<known_answer>EUR 43,476.65.</known_answer>" in content
    assert f"<answer>\n{answer}\n</answer>" in content
    assert content.endswith(
        '<citation number="1">\n<sentence>**EUR 43,476.65** is due [P7].</sentence>\n'
        "<where>Invoice INV-2026-0458 › Totals</where>\n"
        "<passage>| Total due (EUR) | 43,476.65 |</passage>\n</citation>\n\n"
        '<citation number="2">\n<sentence>It is payable in 30 days. [P8][P7]</sentence>\n'
        "<where>Invoice INV-2026-0458 › Terms</where>\n"
        "<passage>Payment terms: 30 days.</passage>\n</citation>\n\n"
        '<citation number="3">\n<sentence>It is payable in 30 days. [P8][P7]</sentence>\n'
        "<where>Invoice INV-2026-0458 › Totals</where>\n"
        "<passage>| Total due (EUR) | 43,476.65 |</passage>\n</citation>"
    )
    assert judged == Judged(
        True,
        "As known.",
        [
            Support("**EUR 43,476.65** is due [P7].", 7, True, "It says the total."),
            Support("It is payable in 30 days. [P8][P7]", 8, True, "It says 30 days."),
            Support(
                "It is payable in 30 days. [P8][P7]", 7, False, "It is the total, not the terms."
            ),
        ],
        judged.usage,
    )
    assert judged.usage.calls == 1

    # A citation the judge gave no verdict on is left unjudged.
    asked.clear()

    class Terse(FakeLLM):
        def structured(self, content, schema, usage=None):
            asked.append(content)
            return Judgement(reason="Wrong total.", right=False, citations=[])

    terse = judge(Terse(), golden, "EUR 43,000 [P7].", passages)
    assert terse.right is False and terse.reason == "Wrong total."
    assert terse.citations == [Support("EUR 43,000 [P7].", 7)]

    # Without citations the judge is told so; without words it is not asked.
    asked.clear()
    judge(Terse(), golden, "It is EUR 43,476.65.", passages)
    assert asked[0].endswith("</answer>\n\nThe answer cites no passage.")
    asked.clear()
    assert judge(Terse(), golden, " \n", passages) == Judged(False, "There is no answer.")
    assert asked == []

    # A model that fails leaves the answer unjudged, saying why.
    failing = FakeLLM()
    failing.failure = LLMError("The model is down; try again later.")
    unjudged = judge(failing, golden, answer, passages)
    assert unjudged.right is None and unjudged.reason == "The model is down; try again later."
    assert [support.supported for support in unjudged.citations] == [None, None, None]


def test_an_answer_is_cut_into_the_sentences_that_cite():
    answer = (
        "**No.** It bills 340 pallets [P3][P4]. The contract discounts 6% from 250. [P7] "
        "The total is EUR 96,271.00 [P3].\n"
        "| Net total (EUR) | 80,900.00 | [P19] |\n"
        "- PO-7708 was late [P5], and so was PO-7722 [P5][P6].\n"
        "Nothing cited here."
    )
    assert cited_sentences(answer) == [
        ("**No.** It bills 340 pallets [P3][P4].", 3),
        ("**No.** It bills 340 pallets [P3][P4].", 4),
        ("The contract discounts 6% from 250. [P7]", 7),
        ("The total is EUR 96,271.00 [P3].", 3),
        ("| Net total (EUR) | 80,900.00 | [P19] |", 19),
        ("- PO-7708 was late [P5], and so was PO-7722 [P5][P6].", 5),
        ("- PO-7708 was late [P5], and so was PO-7722 [P5][P6].", 6),
    ]


def test_the_report_says_where_the_judge_doubts_or_could_not_judge():
    def asked(question, misses, judged, answer="An answer [P1]."):
        golden = Golden(question, "Known.", ())
        return Asked(golden, answer, misses, 1.0, ModelUsage(), judged)

    usage = ModelUsage()
    usage.add("anthropic:judge", RequestUsage(input_tokens=1200, output_tokens=80))
    usage.add("anthropic:judge", RequestUsage(input_tokens=1000, output_tokens=70))
    run = GoldenRun(
        [
            asked("Right?", [], Judged(True, "As known.", [Support("An answer [P1].", 1, True)])),
            asked(
                "Worded otherwise?",
                ['does not say "43,476.65"'],
                Judged(True, "It gives 43 476,65.", [Support("An answer [P1].", 1, None)], usage),
            ),
            asked("Down?", [], Judged(None, "The model is down.")),
            asked(
                "Wrong?",
                [],
                Judged(
                    False,
                    "It gives the net total.",
                    [
                        Support("An answer [P1].", 1, False, "It says the VAT."),
                        Support("An answer [P1].", 2, True),
                    ],
                ),
            ),
        ]
    )
    assert describe_golden(run) == [
        "  ok      Right? (1.0 s)",
        "  FAILED  Worded otherwise? (1.0 s)",
        '          does not say "43,476.65"',
        "          judged right: It gives 43 476,65.",
        "          | An answer [P1].",
        "  ok      Down? (1.0 s)",
        "          not judged: The model is down.",
        "  ok      Wrong? (1.0 s)",
        "          judged wrong: It gives the net total.",
        '          P1 does not support "An answer [P1].": It says the VAT.',
        "          | An answer [P1].",
        f"Prompt versions: answer {ANSWER_PROMPT_VERSION}, judge {JUDGE_PROMPT_VERSION}.",
        "Judged by anthropic:judge: 2 calls, 2,200 tokens in, 150 out.",
        "The judge found 2 of 3 answers right (1 not judged), and 2 of 3 citations supported by "
        "the passage cited (67%).",
        "3 of 4 golden questions answered as known.",
    ]


def test_a_run_without_a_model_is_refused(session):
    reader = Reader()
    reader.available = False
    with pytest.raises(RuntimeError, match="answered by a model, and none answers"):
        run_golden(get_settings(), reader, FakeEmbedder())
    assert session.scalar(select(func.count()).select_from(Organization)) == 0
