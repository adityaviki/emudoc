"""The load test's own parts (loadtest/, frontend/e2e/standin.py): its documents read as the
app reads them, and the stand-in's --fill answers what the indexing stages send, so that a
change to either fails here rather than in the next run, whose figures would quietly be of
empty indexes."""

import importlib.util
from types import SimpleNamespace

from app.config import ROOT_DIR
from app.extraction import BUILT_IN, chosen
from app.indexing import enrich
from app.indexing.enrich import DocumentAnalysis, InferredSections, TemplatedDocumentAnalysis
from app.llm import OCR_PROMPT, OcrPage
from app.parsing import parse_file
from loadtest import corpus
from loadtest.__main__ import _share_found
from loadtest.report import Report, rank

_spec = importlib.util.spec_from_file_location("standin", ROOT_DIR / "frontend/e2e/standin.py")
standin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standin)


def asked(schema_of, content):
    """The stand-in's --fill reply to an indexing request for `schema_of`, validated as the
    app validates a model's."""
    schema = schema_of.model_json_schema()
    messages = [{"role": "user", "content": content}]
    return schema_of.model_validate(standin.filled(schema, schema.get("$defs", {}), messages))


def test_the_library_s_documents_state_their_fields_in_blocks_of_their_own(tmp_path):
    paths = corpus.library(tmp_path, 40)

    assert {path.suffix for path in paths} == {".md", ".pdf", ".docx"}
    read = {
        path.name: [block.text for block in parse_file(path, path.name).blocks] for path in paths
    }
    for name, texts in read.items():
        fields = [text for text in texts if standin.FIELD.match(text)]
        # Every kind has a few fields, which the stand-in turns into facts, each its own block.
        assert len(fields) >= 2 and all(text.count(": ") == 1 for text in fields), (name, texts)
    # The same seed makes the same documents (a PDF's bytes carry the moment it was made).
    again = corpus.library(tmp_path / "again", 40)
    assert {
        path.name: [b.text for b in parse_file(path, path.name).blocks] for path in again
    } == read


def test_the_standin_reads_a_document_as_enrich_sends_it():
    document = SimpleNamespace(filename="invoice-00001.md", page_count=1)
    section = SimpleNamespace(id=7, path="Invoice INV-2026-00001")
    texts = [
        "Invoice INV-2026-00001",
        "- Total amount: EUR 1,250.00",
        "- Due date: 2026-11-30",
        "- Supplier: Northwind Fasteners Ltd",
        "Payment is due within 30 days of the invoice date. By transfer.",
    ]
    blocks = [
        SimpleNamespace(ordinal=n, page=1, kind="paragraph", text=text)
        for n, text in enumerate(texts)
    ]
    prompt = enrich._render(document, [(section, enrich._condensed(blocks))], whole=True)

    analysis = asked(DocumentAnalysis, prompt)

    assert (analysis.title, analysis.doc_type, analysis.doc_date) == (
        "Invoice INV-2026-00001",
        "invoice",
        "2026-11-30",
    )
    assert analysis.summary.startswith("- Total amount: EUR 1,250.00")
    assert [(s.section, s.summary) for s in analysis.sections] == [(7, "Invoice INV-2026-00001")]
    assert [(f.name, f.type, f.number, f.date, f.currency, f.block) for f in analysis.facts] == [
        ("total_amount", "money", 1250.0, None, "EUR", 1),
        ("due_date", "date", None, "2026-11-30", None, 2),
        ("supplier", "text", None, None, None, 3),
    ]
    # Each quote is its block, which the enrich stage takes as verified.
    assert [fact.quote for fact in analysis.facts] == texts[1:4]


def test_the_standin_names_the_listed_template_a_document_is_and_reads_its_fields():
    """The browser test's stand-in, in its default mode, as the enrich stage asks it with the
    project's templates listed: the one the opening words name, with the "Name: value" facts."""
    schema = TemplatedDocumentAnalysis.model_json_schema()

    def asked_plainly(texts: list[str]) -> TemplatedDocumentAnalysis:
        document = SimpleNamespace(filename="invoice.md", page_count=None)
        section = SimpleNamespace(id=7, path=texts[0])
        blocks = [
            SimpleNamespace(ordinal=n, page=None, kind="paragraph", text=text)
            for n, text in enumerate(texts)
        ]
        prompt = (
            f"{enrich._render(document, [(section, enrich._condensed(blocks))], whole=True)}\n\n"
            f"{enrich.TEMPLATE_INSTRUCTIONS}\n\n{enrich._listing(BUILT_IN)}"
        )
        messages = [{"role": "user", "content": prompt}]
        reply = standin.instance(schema, schema.get("$defs", {}))
        return TemplatedDocumentAnalysis.model_validate(standin.named(reply, schema, messages))

    analysis = asked_plainly(
        [
            "Invoice INV-2026-0412",
            "From Acme Logistics Ltd, for the deliveries of March.",
            "Invoice number: INV-2026-0412",
            "Total amount: EUR 1,250.00",
        ]
    )
    assert chosen(BUILT_IN, analysis.template) is BUILT_IN[0]
    assert [(f.name, f.value, f.number) for f in analysis.facts] == [
        ("invoice_number", "INV-2026-0412", None),
        ("total_amount", "EUR 1,250.00", 1250.0),
    ]
    # Otherwise the smallest reply, as for any other request of the default mode.
    assert (analysis.title, analysis.sections) == ("", [])

    letter = asked_plainly(["Dear Ms Hopper,", "Total amount: EUR 9.00"])
    assert (letter.template, letter.facts) == (None, [])


def test_the_standin_summarises_a_long_table_shown_in_part_and_takes_no_facts_from_it():
    document = SimpleNamespace(filename="orders.xlsx", page_count=None)
    section = SimpleNamespace(id=3, path="orders › Orders 1")
    header = "| Order | Total amount |\n| --- | --- |"
    blocks = [
        SimpleNamespace(
            ordinal=n,
            page=None,
            kind="table",
            text="\n".join([header, *(f"| ORD-{n}-{row} | EUR {row}.00 |" for row in range(200))]),
        )
        for n in range(1, 4)
    ]
    prompt = enrich._render(document, [(section, enrich._condensed(blocks))], whole=True)

    analysis = asked(DocumentAnalysis, prompt)

    assert [(s.section, s.summary.split(",")[0]) for s in analysis.sections] == [
        (3, "A table of 600 rows in blocks B1 to B3")
    ]
    assert analysis.facts == []


def test_the_standin_leaves_the_sections_of_a_document_without_headings_where_they_are():
    assert asked(InferredSections, "This document has no headings.").sections == []


def test_the_standin_transcribes_each_scanned_page_as_a_page_of_its_own():
    def page(picture: str) -> OcrPage:
        image = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{picture}"}}
        return asked(OcrPage, [image, {"type": "text", "text": OCR_PROMPT}])

    one = page("AAAA")

    assert one.blocks[0].kind == "heading" and one.blocks[0].level == 1
    assert standin.FIELD.match(one.blocks[1].text)
    assert len(" ".join(block.text for block in one.blocks).split()) >= 128
    assert page("AAAA") == one
    assert page("BBBB") != one


def test_a_percentile_is_a_wait_a_member_had():
    values = [float(n) for n in range(1, 101)]

    assert (rank(values, 0.5), rank(values, 0.95), rank([3.0], 0.95), rank([], 0.95)) == (
        50.0,
        95.0,
        3.0,
        None,
    )
    report = Report()
    report.add("search", "Search", [0.2, None, 1.5])
    assert report.markdown().splitlines()[2] == "| search | Search | 2 | 200 ms | 1.50 s | 1.50 s |"


def test_a_candidate_as_near_as_the_nearest_counts_as_one_of_them():
    exact = [0.1, 0.2, 0.2, 0.3]

    # Another passage at the 2nd's distance is as near as the 3rd it stands in for.
    assert _share_found([0.1, 0.2, 0.2], exact, 3) == 1.0
    assert _share_found([0.1, 0.25, 0.3], exact, 3) == 1 / 3
    assert _share_found([0.15, 0.2], exact, 1) == 0.0
    # A project of fewer passages than asked for is measured by what it has.
    assert _share_found([0.1, 0.2, 0.2, 0.3], exact, 10) == 1.0
    assert _share_found([], [], 10) == 1.0
