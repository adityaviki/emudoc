"""The golden questions: questions about the sample project's documents whose answers are known,
asked of the agent as a member's question is and graded on what each answer must say and the
documents it must cite (`python -m app.maintenance golden`). The Models workflow
(.github/workflows/models.yml) asks them every morning and once a change to the prompts or the
agent is on main, so that a prompt, a model or a provider that answers worse shows here before a
member reads it.

The documents are the sample's (app/sample), the canonical scenario of an invoice checked against
the contract, the price list and the delivery log: its index is copied into an organisation made
for the run, as a sign-up copies it, and deleted with it after, so the run asks no indexing
model, records nothing as an organisation's usage, and costs the answers alone. Each document is
embedded first when the embeddings endpoint answers; without it the agent's search matches words
alone, which the report says.

An answer is graded by its words, not by a model: each question names the figures and names a
right answer states, each in the ways it may be written, of which the answer must hold one, and
the documents at least one of whose passages it must cite; a reference to a passage no tool
showed it fails it too. A right answer worded in a way the question does not list fails as well:
the report prints a failed answer whole, and the way it was written joins the list. Judging
correctness by a model, and citations by whether the passage supports the sentence, are the
quality program's next steps (the checklist's 3.14).
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import delete

from app.agent.loop import run_agent
from app.config import Settings, listing
from app.db import SessionLocal
from app.drafts import CITATION
from app.embeddings import Embedder
from app.indexing.pipeline import Services, process_document
from app.llm import LLM
from app.models import Organization
from app.sample import QUESTIONS, copy_sample
from app.storage import LocalStorage
from app.usage import ModelUsage, money

CONTRACT = "acme-service-contract.pdf"
DELIVERY_LOG = "delivery-log.xlsx"
EMAIL = "email-late-delivery.txt"
INVOICES = (
    "invoice-INV-2026-0412.pdf",
    "invoice-INV-2026-0458.pdf",
    "invoice-INV-2026-0503.pdf",
)
PRICE_LIST = "price-list.csv"
MINUTES = "supplier-review-meeting.docx"

# The ways an answer says that the documents do not, or that something is not so; and that it is.
NO = ("no", "not", "none", "nothing", "never", "without", "doesn't", "isn't", "cannot", "can't")
YES = ("yes", "match", "matches", "matching", "same", "consistent", "agree", "agrees", "correct")
# The ways a percentage is written.
SIX_PERCENT = ("6%", "6 per cent", "6 percent", "six per cent", "six percent")


@dataclass(frozen=True)
class Golden:
    """A question with a known answer: what a right answer says, each entry the ways one thing
    may be written, and the documents, by file name, whose passages it cites."""

    question: str
    says: tuple[tuple[str, ...], ...]
    cites: tuple[str, ...] = ()


GOLDEN = (
    # The three an empty chat of the sample offers, as every new member sees them. Invoice
    # INV-2026-0503 bills 210 and 130 pallets, past the contract's 250 for its 6 %, with no
    # discount; two deliveries were late, by a carrier's strike, and the contract sets no credit
    # for that; and every unit price is the list's.
    Golden(QUESTIONS[0], (SIX_PERCENT, ("340", "250"), NO), (INVOICES[2], CONTRACT)),
    Golden(
        QUESTIONS[1], (("PO-7708",), ("PO-7722",), ("strike", "strikes")), (DELIVERY_LOG, MINUTES)
    ),
    Golden(QUESTIONS[2], (YES,), (PRICE_LIST, *INVOICES)),
    # A detail of one document.
    Golden("What is the total due on invoice INV-2026-0458?", (("43,476.65",),), (INVOICES[1],)),
    Golden(
        "How much notice must a party give to end the contract for convenience?",
        (("90", "ninety"),),
        (CONTRACT,),
    ),
    Golden("What is the supplier's liability capped at?", (("250,000",),), (CONTRACT,)),
    Golden(
        "What interest do late payments accrue under the contract?",
        (("1.5%", "1.5 per cent", "1.5 percent"),),
        (CONTRACT,),
    ),
    Golden(
        "How late did order PO-7708 arrive, according to the email?",
        (("three days", "3 days"),),
        (EMAIL,),
    ),
    Golden(
        "What was the on-time delivery rate in August, and what was the target?",
        (("94%",), ("97%",)),
        (MINUTES,),
    ),
    Golden(
        "Who is to review the liability cap with legal, and by when?",
        (("Jonas Becker",), ("15 October", "October 15", "2026-10-15", "15.10.2026")),
        (MINUTES,),
    ),
    # Worked out from what the documents say: every invoice summed, and the term from its start.
    Golden(
        "What do the three invoices come to in total, VAT included?",
        (("151,528.65",),),
        INVOICES,
    ),
    Golden("When does the contract's initial term end?", (("2028",),), (CONTRACT,)),
    # Not in the documents: the answer says so rather than make one up.
    Golden("What insurance does the contract require the supplier to carry?", (NO,)),
)


@dataclass
class Asked:
    """One golden question as the agent answered it: its words, what the grading found it
    lacks, how long it took and what it used."""

    golden: Golden
    answer: str
    misses: list[str]
    seconds: float
    usage: ModelUsage

    @property
    def passed(self) -> bool:
        return not self.misses


@dataclass
class GoldenRun:
    """Every question asked, and why the search matched words alone, None when it had the
    passages' vectors."""

    asked: list[Asked] = field(default_factory=list)
    by_words: str | None = None

    @property
    def failed(self) -> list[Asked]:
        return [asked for asked in self.asked if not asked.passed]


def run_golden(
    settings: Settings,
    llm: LLM | None = None,
    embedder: Embedder | None = None,
    questions: tuple[Golden, ...] = GOLDEN,
) -> GoldenRun:
    """Ask each question of the agent over a copy of the sample project, in an organisation
    made for the run and deleted after it with whatever the run recorded. Refused with
    `RuntimeError` without a model."""
    llm = llm or LLM(settings)
    if not llm.available:
        raise RuntimeError(
            f"The golden questions are answered by a model, and none answers: {llm.problem()}"
        )
    embedder = embedder or Embedder(settings)
    run = GoldenRun()
    with TemporaryDirectory(prefix="emudoc-golden-") as directory:
        services = Services(llm, embedder, LocalStorage(Path(directory)))
        with SessionLocal() as session:
            organization = Organization(
                name="Golden questions", slug=f"golden-{uuid4().hex[:12]}", plan="enterprise"
            )
            session.add(organization)
            session.flush()
            org_id = organization.id
            project, documents = copy_sample(session, organization, services.storage)
            project_id = project.id
            names = {document.id: document.filename for document in documents}
            session.commit()
        try:
            run.by_words = _embed(services, list(names))
            run.asked = [_ask(services, project_id, names, golden) for golden in questions]
        finally:
            with SessionLocal() as session:
                session.execute(delete(Organization).where(Organization.id == org_id))
                session.commit()
    return run


def _embed(services: Services, document_ids: list[int]) -> str | None:
    """Embed the copied documents, as a sign-up's index jobs do; why not, when it could not."""
    if unreachable := services.embedder.check():
        return unreachable
    for document_id in document_ids:
        if failures := process_document(document_id, services):
            return failures[0].message
    return None


def _ask(services: Services, project_id: int, names: dict[int, str], golden: Golden) -> Asked:
    usage = ModelUsage()
    words: list[str] = []
    errors: list[str] = []
    # The passages a tool showed the model, by id, with the document each is of.
    shown: dict[int, int] = {}
    started = time.monotonic()
    # As an answer runs (`answer` in app/answers.py), offered the questions to ask next.
    turn = [{"role": "user", "content": golden.question}]
    for event in run_agent(services, project_id, turn, usage, following=True):
        if event["type"] == "text":
            words.append(event["text"])
        elif event["type"] == "sources":
            shown.update({source["id"]: source["document_id"] for source in event["sources"]})
        elif event["type"] == "error":
            errors.append(f"ended with an error: {event['message']}")
    answer = "".join(words)
    references = [int(found) for found in CITATION.findall(answer)]
    cited = {names[shown[reference]] for reference in references if reference in shown}
    invented = sorted({reference for reference in references if reference not in shown})
    misses = errors + grade(golden, answer, cited, invented)
    return Asked(golden, answer, misses, time.monotonic() - started, usage)


def grade(golden: Golden, answer: str, cited: set[str], invented: list[int]) -> list[str]:
    """What the answer lacks of a right one, in the report's words; none when it is right."""
    said = plain(answer)
    misses = [
        f"does not say {_either(ways)}"
        for ways in golden.says
        if not any(holds(said, way) for way in ways)
    ]
    misses += [f"cites no passage of {name}" for name in golden.cites if name not in cited]
    if invented:
        misses.append(
            f"cites {listing([f'P{reference}' for reference in invented])}, which no tool showed it"
        )
    return misses


def _either(ways: tuple[str, ...]) -> str:
    quoted = [f'"{way}"' for way in ways]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}" if len(quoted) > 1 else quoted[0]


# The thousands separators and spaces inside a figure ("43,476.65", "250 000"), and the space
# some write before a percent sign.
_SEPARATOR = re.compile(r"(?<=\d)[,\s](?=\d{3}(?!\d))")
_BEFORE_PERCENT = re.compile(r"(?<=\d)\s+%")


def plain(text: str) -> str:
    """Text as the grading compares it: lowercase, one kind of space and apostrophe, figures
    without their separators."""
    # `\s` is any space, the non-breaking ones a figure is often written with among them.
    text = text.lower().replace("\u2019", "'")
    text = _BEFORE_PERCENT.sub("%", _SEPARATOR.sub("", text))
    return re.sub(r"\s+", " ", text)


def holds(said: str, way: str) -> bool:
    """Whether plain text holds one way of saying a thing as whole words and figures: "6%" is
    not in "16%", nor "no" in "note"."""
    return re.search(rf"(?<!\w){re.escape(plain(way))}(?!\w)", said) is not None


def describe_golden(run: GoldenRun) -> list[str]:
    """The lines the `golden` command prints: each question's verdict with what a failed answer
    lacks and its words, what the answers used, and a last line that says how many were right."""
    lines: list[str] = []
    total = ModelUsage()
    for asked in run.asked:
        total.include(asked.usage)
        verdict = "ok" if asked.passed else "FAILED"
        lines.append(f"  {verdict:6}  {asked.golden.question} ({asked.seconds:.1f} s)")
        if not asked.passed:
            lines.append(f"          {'; '.join(asked.misses)}")
            for line in (asked.answer.strip() or "(no answer)").splitlines():
                lines.append(f"          | {line}")
    if run.by_words:
        lines.append(
            f"The search matched words alone, since the passages could not be embedded: "
            f"{run.by_words}"
        )
    if total.calls:
        spent = total.spent()
        used = (
            f"Answered by {listing(list(total.models))}: {spent['calls']} calls, "
            f"{spent['input_tokens']:,} tokens in, {spent['output_tokens']:,} out"
        )
        cost = spent["cost_micros"]
        lines.append(f"{used}, {money(cost)}." if cost is not None else f"{used}.")
    right = len(run.asked) - len(run.failed)
    lines.append(
        f"{right} of {len(run.asked)} golden questions answered as known."
        if run.failed
        else f"Every golden question answered as known ({len(run.asked)} asked)."
    )
    return lines
