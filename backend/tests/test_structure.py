from app.indexing.structure import MAX_CHARS, TARGET_CHARS, SectionHint, build_outline
from app.parsing.types import ParsedBlock


def heading(text, level):
    return ParsedBlock("heading", text, level=level)


def para(text, page=None):
    return ParsedBlock("paragraph", text, page=page)


def test_headings_become_a_tree_and_the_lone_top_heading_becomes_the_title():
    blocks = [
        heading("Annual Report", 1),
        para("Intro text."),
        heading("Finance", 2),
        para("Revenue grew."),
        heading("Costs", 3),
        para("Costs fell."),
        heading("People", 2),
        para("Headcount is stable."),
    ]
    outline = build_outline(blocks, "report")

    assert outline.title == "Annual Report"
    assert [(s.title, s.level) for s in outline.sections] == [
        ("Overview", 1),
        ("Finance", 1),
        ("Costs", 2),
        ("People", 1),
    ]
    finance, costs = outline.sections[1], outline.sections[2]
    assert costs.parent is finance
    # A section spans its subsections; its own content stops at the first of them.
    assert (finance.block_start, finance.block_end) == (2, 6)
    assert (finance.content_start, finance.content_end) == (3, 4)
    assert costs.path(outline.title) == "Annual Report › Finance › Costs"
    assert [p.text for p in outline.passages] == [
        "Intro text.",
        "Revenue grew.",
        "Costs fell.",
        "Headcount is stable.",
    ]
    assert outline.passages[2].section is costs


def test_repeated_top_level_headings_are_all_sections():
    blocks = [heading("Part A", 1), para("a"), heading("Part B", 1), para("b")]
    outline = build_outline(blocks, "fallback")
    assert outline.title == "fallback"
    assert [s.title for s in outline.sections] == ["Part A", "Part B"]


def test_short_document_without_headings_is_one_section():
    outline = build_outline([para("Dear Sir,"), para("Thank you.")], "letter")
    assert [(s.title, s.block_start, s.block_end) for s in outline.sections] == [("letter", 0, 2)]
    assert len(outline.passages) == 1
    assert outline.passages[0].text == "Dear Sir,\n\nThank you."


def test_long_document_without_headings_falls_back_to_pages():
    blocks = [para(f"Text on page {page}.", page=page) for page in (1, 1, 2, 3)]
    outline = build_outline(blocks, "scan", page_count=3)
    assert [s.title for s in outline.sections] == ["Page 1", "Page 2", "Page 3"]
    assert [(s.page_start, s.page_end) for s in outline.sections] == [(1, 1), (2, 2), (3, 3)]


def test_hints_define_sections_when_there_are_no_headings():
    blocks = [para(f"Paragraph {n}.") for n in range(6)]
    hints = [SectionHint(3, "Second half"), SectionHint(0, "First half")]
    outline = build_outline(blocks, "notes", hints=hints)
    assert [(s.title, s.block_start, s.block_end) for s in outline.sections] == [
        ("First half", 0, 3),
        ("Second half", 3, 6),
    ]
    # A hinted section starts at a content block, so that block belongs to its passages.
    assert outline.passages[1].text.startswith("Paragraph 3.")


def test_long_paragraph_is_split_at_sentence_ends():
    sentence = "This sentence is exactly long enough to be useful for the test. "
    text = (sentence * 60).strip()
    outline = build_outline([heading("Only", 1), para(text)], "doc")

    assert len(outline.passages) > 1
    assert all(len(p.text) <= MAX_CHARS for p in outline.passages)
    assert all(p.text.endswith(".") for p in outline.passages)
    assert " ".join(p.text for p in outline.passages) == text
    # Every piece still points at the one block it came from.
    assert {(p.block_start, p.block_end) for p in outline.passages} == {(1, 2)}


def test_tables_stay_whole_and_apart_from_prose():
    table = ParsedBlock("table", "| a | b |\n| --- | --- |\n| 1 | 2 |")
    outline = build_outline([para("Before."), table, para("After.")], "doc")
    assert [p.text for p in outline.passages] == ["Before.", table.text, "After."]


def test_long_table_is_split_with_its_header_repeated():
    rows = "\n".join(f"| row {n} | {'x' * 60} |" for n in range(80))
    table = ParsedBlock("table", f"| name | value |\n| --- | --- |\n{rows}")
    outline = build_outline([table], "doc")

    assert len(outline.passages) > 1
    for passage in outline.passages:
        assert passage.text.startswith("| name | value |\n| --- | --- |\n")
        assert len(passage.text) <= MAX_CHARS


def test_paragraphs_fill_passages_up_to_the_target_and_a_short_tail_is_folded_in():
    blocks = [para("a" * 500), para("b" * 500), para("c" * 500), para("short tail")]
    outline = build_outline(blocks, "doc")

    assert [len(p.text) for p in outline.passages][0] <= TARGET_CHARS
    assert outline.passages[-1].text.endswith("short tail")
    assert len(outline.passages) == 2
    assert (outline.passages[1].block_start, outline.passages[1].block_end) == (2, 4)
