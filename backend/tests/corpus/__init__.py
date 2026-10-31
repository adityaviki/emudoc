"""The parse corpus: files of every kind the parsers take, and of the ways a kind is hard to read,
each with a snapshot of what the parse and structure stages make of it (`parsed/`), to which
tests/test_corpus.py holds it. A change to a parser, or to a library it reads with (a Dependabot
update of pdfplumber, python-docx or openpyxl), that reads a file differently fails the suite,
whether the change was meant or not.

The files are the sample project's (app/sample/files), the HEIC photo of tests/fixtures, and
`files/`: written once on 2026-10-31 by the builders the parsing tests use (tests/conftest.py,
test_parsing.py, test_scans.py), the receipt and the checklist copied from the workspace's
sample-documents. They stay as they are, so a snapshot changes only when the reading does. A
file added to `files/` gets its snapshot from the command below, and one taken away loses it.

A snapshot keeps what the stages store and nothing a model makes: the parse (each block's kind,
level, page, box rounded as the pipeline keeps it, and text; the pages and their sizes, the
title, the pages left to OCR), the language its words are detected in, and the outline that
`build_outline` makes of those blocks without the sections a model infers for a document with no
headings. After a change meant to read files differently, the snapshots are written again with

    uv --directory emudoc/backend run python -m tests.corpus

and the changes read in the diff and committed with it.
"""

import json
from pathlib import Path
from typing import Any

from app.indexing.structure import build_outline
from app.languages import detect_language
from app.parsing import parse_file
from app.sample import FILES as SAMPLE_FILES

HERE = Path(__file__).parent
FILES = HERE / "files"
PARSED = HERE / "parsed"
PHOTO = HERE.parent / "fixtures" / "photo.heic"


def corpus() -> list[Path]:
    """Every file of the corpus, by name."""
    files = [*SAMPLE_FILES.iterdir(), *FILES.iterdir(), PHOTO]
    return sorted(files, key=lambda path: path.name)


def snapshot_path(path: Path) -> Path:
    return PARSED / f"{path.name}.json"


def snapshot_of(path: Path) -> str:
    """What the parse and structure stages make of the file, written as its snapshot is."""
    parsed = parse_file(path, path.name)
    detected = detect_language((block.text for block in parsed.blocks), ())
    made: dict[str, Any] = {
        "title": parsed.title,
        "language": detected.code if detected else None,
        "page_count": parsed.page_count,
        "page_sizes": [[round(width, 2), round(height, 2)] for width, height in parsed.page_sizes],
        "ocr_pages": parsed.ocr_pages,
        "blocks": [
            {
                "kind": block.kind,
                "level": block.level,
                "page": block.page,
                "bbox": [round(value, 2) for value in block.bbox] if block.bbox else None,
                "text": block.text,
            }
            for block in parsed.blocks
        ],
    }
    # A file of scans alone has no blocks before OCR, and the pipeline outlines none.
    if parsed.blocks:
        outline = build_outline(parsed.blocks, parsed.title or path.stem, parsed.page_count)
        place = {id(section): index for index, section in enumerate(outline.sections)}
        made["outline"] = {
            "title": outline.title,
            "sections": [
                {
                    "title": section.title,
                    "level": section.level,
                    "parent": place[id(section.parent)] if section.parent else None,
                    "blocks": [section.block_start, section.block_end],
                    "pages": [section.page_start, section.page_end],
                }
                for section in outline.sections
            ],
            "passages": [
                {
                    "section": place[id(passage.section)],
                    "blocks": [passage.block_start, passage.block_end],
                    "pages": [passage.page_start, passage.page_end],
                    "text": passage.text,
                }
                for passage in outline.passages
            ],
        }
    return _written(made) + "\n"


def _written(value: Any, indent: str = "") -> str:
    """JSON that a diff reads: an object a key a line, and a list of objects an object a line."""
    inner = indent + "  "
    if isinstance(value, dict):
        lines = [
            f"{inner}{json.dumps(key)}: {_written(item, inner)}" for key, item in value.items()
        ]
        return "{\n" + ",\n".join(lines) + f"\n{indent}}}"
    if value and isinstance(value, list) and all(isinstance(item, dict) for item in value):
        lines = [inner + json.dumps(item, ensure_ascii=False) for item in value]
        return "[\n" + ",\n".join(lines) + f"\n{indent}]"
    return json.dumps(value, ensure_ascii=False)


def write() -> None:
    """Write every file's snapshot again, take away those of files no longer in the corpus, and
    say what changed."""
    PARSED.mkdir(exist_ok=True)
    left = {path.name: path for path in PARSED.glob("*.json")}
    added, changed = [], []
    for path in corpus():
        target = snapshot_path(path)
        left.pop(target.name, None)
        made = snapshot_of(path)
        if not target.exists():
            added.append(path)
        elif target.read_text(encoding="utf-8") != made:
            changed.append(path)
        else:
            continue
        target.write_text(made, encoding="utf-8")
    for name, stale in sorted(left.items()):
        stale.unlink()
        print(f"Took away {name}: its file is no longer in the corpus.")
    for path in added:
        print(f"Wrote {snapshot_path(path).name}, a new file's.")
    for path in changed:
        print(f"Wrote {snapshot_path(path).name} again: the file is read differently.")
    if any(path.parent == SAMPLE_FILES for path in changed):
        # The golden questions are asked over the shipped index, not over a fresh reading.
        print(
            "The sample's shipped index (app/sample/index.json) holds the reading from before: "
            "`python -m app.maintenance build-sample` writes it again, which costs model calls."
        )
    if not (added or changed or left):
        print("Every file of the corpus is read as its snapshot says.")
