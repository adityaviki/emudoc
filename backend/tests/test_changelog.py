"""CHANGELOG.md keeps one shape, which its introduction states: read here line by line, so that
an entry written otherwise, or two branches' days met out of order, fails the suite. The file is
read by people alone since the page of what's new was taken out (2026-11-11)."""

import re
from dataclasses import dataclass
from datetime import date

import pytest

from app.config import ROOT_DIR

CHANGELOG = ROOT_DIR / "CHANGELOG.md"

_HEADING = re.compile(r"## (\d{4}-\d{2}-\d{2})(?: to (\d{4}-\d{2}-\d{2}))?")
_PARTS = {"Members:": "members", "Operators:": "operators"}
# An entry's line after its first: two spaces, then its words.
_GOES_ON = re.compile(r"  \S.*")


@dataclass(frozen=True)
class Day:
    # The first and the last day the entries were made on, the same for a single day.
    first: date
    last: date
    # Each entry joined into one line, the day's newest first.
    members: tuple[str, ...]
    operators: tuple[str, ...]


def parse(text: str) -> list[Day]:
    """The days of a changelog, newest first, as the file writes them. A line of another shape,
    or a day out of order, raises ValueError naming its line."""
    found: list[tuple[date, date, dict[str, list[str]]]] = []
    entries: list[str] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            heading = _HEADING.fullmatch(line)
            if not heading:
                raise _misshapen(number, "a day's heading is ## YYYY-MM-DD or ## YYYY-MM-DD to …")
            first = date.fromisoformat(heading[1])
            last = date.fromisoformat(heading[2] or heading[1])
            if first > last or (found and last >= found[-1][0]):
                raise _misshapen(number, "the days go from the newest to the oldest")
            found.append((first, last, {part: [] for part in _PARTS.values()}))
            entries = None
        elif not found or not line:
            # The introduction, and the blank lines between the lists and their entries.
            continue
        elif line in _PARTS:
            entries = found[-1][2][_PARTS[line]]
        elif entries is not None and line.startswith("- "):
            entries.append(line.removeprefix("- ").strip())
        elif entries and _GOES_ON.fullmatch(line):
            entries[-1] += " " + line.strip()
        else:
            raise _misshapen(
                number,
                "under a day come Members: and Operators:, each a list of entries that start "
                "with - and go on indented by two spaces",
            )
    return [
        Day(first, last, tuple(parts["members"]), tuple(parts["operators"]))
        for first, last, parts in found
    ]


def _misshapen(number: int, rule: str) -> ValueError:
    return ValueError(f"Line {number} of CHANGELOG.md is not of the file's shape: {rule}.")


def test_the_changelog_keeps_its_shape():
    text = CHANGELOG.read_text()
    # Strict: a line of another shape raises, naming it.
    days = parse(text)
    assert days and len(days) == len(re.findall(r"^## ", text, re.MULTILINE))
    assert [day.last for day in days] == sorted((day.last for day in days), reverse=True)
    # Every entry is read, each one line, whatever lines it was wrapped over.
    for day, written_day in zip(days, re.split(r"^## ", text, flags=re.MULTILINE)[1:], strict=True):
        members = written_day.partition("Members:\n")[2].partition("Operators:\n")[0]
        assert len(day.members) == len(re.findall(r"^- ", members, re.MULTILINE)), day.last
    assert all(
        entry and "\n" not in entry and "  " not in entry
        for day in days
        for entry in day.members + day.operators
    )


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("## 21 October 2026\n", 1),
        ("## 2026-10-20\n\nMembers:\n\n- One\n\n## 2026-10-21\n", 7),
        ("## 2026-10-21\n\nMembers:\n\n- One\n\n## 2026-10-21\n", 7),
        ("## 2026-10-21 to 2026-10-20\n", 1),
        ("## 2026-10-21\n\n- An entry outside a list\n", 3),
        ("## 2026-10-21\n\nMembers:\n\n- One\n   too far in\n", 6),
        ("## 2026-10-21\n\nMembers:\n\n* Another kind of list\n", 5),
        ("## 2026-10-21\n\nAdmins:\n\n- One\n", 3),
    ],
)
def test_a_line_of_another_shape_is_refused_by_its_number(text, line):
    with pytest.raises(ValueError, match=f"^Line {line} of CHANGELOG.md "):
        parse(text)


def test_entries_are_joined_and_spans_of_days_read():
    first, span = parse(
        "# Changelog\n\nAn introduction\nof two lines.\n\n"
        "## 2026-10-21\n\nMembers:\n\n- One entry\n  over two lines.\n- Another.\n\n"
        "Operators:\n\n- For operators.\n\n"
        "## 2026-10-18 to 2026-10-20\n\nOperators:\n\n- Only for operators.\n"
    )
    assert first == Day(
        date(2026, 10, 21),
        date(2026, 10, 21),
        ("One entry over two lines.", "Another."),
        ("For operators.",),
    )
    assert (span.first, span.last, span.members) == (date(2026, 10, 18), date(2026, 10, 20), ())
