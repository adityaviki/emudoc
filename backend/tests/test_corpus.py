"""The parse corpus (tests/corpus): every file is read and outlined as its snapshot says, the
corpus holds a file of every kind the parsers take, and every snapshot is a file's."""

import difflib

import pytest

from app.parsing import IMAGE_TYPES, PARSER_MODULES
from tests.corpus import PARSED, corpus, snapshot_of, snapshot_path

WRITE_AGAIN = "uv --directory emudoc/backend run python -m tests.corpus"
# Enough of a diff to see what changed, short of a file read wholly differently.
SHOWN_LINES = 60


@pytest.mark.parametrize("path", corpus(), ids=lambda path: path.name)
def test_each_file_is_read_as_its_snapshot_says(path):
    kept = snapshot_path(path)
    assert kept.exists(), f"{path.name} has no snapshot: write it with {WRITE_AGAIN}"
    before, now = kept.read_text(encoding="utf-8"), snapshot_of(path)
    if now != before:
        diff = difflib.unified_diff(
            before.splitlines(), now.splitlines(), "snapshot", "read now", n=1, lineterm=""
        )
        pytest.fail(
            f"{path.name} is read differently from its snapshot:\n"
            + "\n".join(list(diff)[:SHOWN_LINES])
            + f"\nIf the change is meant, write the snapshots again with {WRITE_AGAIN}, read "
            "what changed in the diff and commit them with it.",
            pytrace=False,
        )


def test_the_corpus_holds_a_file_of_every_kind_the_parsers_take():
    def read_by(suffix: str) -> str:
        return "an image" if suffix in IMAGE_TYPES else PARSER_MODULES[suffix]

    files = corpus()
    assert {read_by(path.suffix.lower()) for path in files} == {
        *PARSER_MODULES.values(),
        "an image",
    }
    # A snapshot is named by its file, so two files of one name would share it.
    assert len({path.name for path in files}) == len(files)


def test_every_snapshot_is_a_files():
    assert sorted(PARSED.glob("*.json")) == sorted(snapshot_path(path) for path in corpus())
