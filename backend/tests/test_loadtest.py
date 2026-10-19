"""The load test's own parts (loadtest/, frontend/e2e/standin.py): its documents read as the
app reads them, and the stand-in's --fill answers what the indexing stages send, so that a
change to either fails here rather than in the next run, whose figures would quietly be of
empty indexes."""

import importlib.util
from types import SimpleNamespace

from app.config import ROOT_DIR
from app.indexing import enrich
from app.indexing.enrich import DocumentAnalysis, InferredSections
from app.llm import OCR_PROMPT, OcrPage
from app.parsing import parse_file
from loadtest import corpus
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
    blocks = [SimpleNamespace(ordinal=n, page=1, text=text) for n, text in enumerate(texts)]
    prompt = enrich._render(document, [(section, blocks)], whole=True)

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
