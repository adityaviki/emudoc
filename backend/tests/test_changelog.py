"""CHANGELOG.md keeps one shape, which its introduction states: read here line by line, so that
an entry written otherwise, or two releases out of order, fails the suite. People alone read it:
no page of the app shows it (what's new went on 2026-11-11)."""

import re
from dataclasses import dataclass
from datetime import date

import pytest

from app.config import ROOT_DIR

CHANGELOG = ROOT_DIR / "CHANGELOG.md"

UNRELEASED = "## Unreleased"
_RELEASE = re.compile(r"## (\d+)\.(\d+)\.(\d+) \((\d{4}-\d{2}-\d{2})\)")
_PARTS = {"### Users": "users", "### Admins": "admins"}
# An entry's line after its first: two spaces, then its words.
_GOES_ON = re.compile(r"  \S.*")


@dataclass(frozen=True)
class Release:
    # The version, None for what is on main and not released yet.
    version: tuple[int, int, int] | None
    # The day it was released, None until it is.
    day: date | None
    # Each entry joined into one line, the newest first.
    users: tuple[str, ...]
    admins: tuple[str, ...]


def parse(text: str) -> list[Release]:
    """The releases of a changelog, newest first, as the file writes them: Unreleased, if there,
    then each version. A line of another shape, or a release out of order, raises ValueError
    naming its line."""
    found: list[tuple[tuple[int, int, int] | None, date | None, dict[str, list[str]]]] = []
    entries: list[str] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            if line == UNRELEASED:
                if found:
                    raise _misshapen(number, "Unreleased comes first, once")
                version, day = None, None
            elif heading := _RELEASE.fullmatch(line):
                version = (int(heading[1]), int(heading[2]), int(heading[3]))
                day = date.fromisoformat(heading[4])
                before = found[-1] if found else None
                if before and before[0] and (version >= before[0] or day > before[1]):
                    raise _misshapen(number, "the releases go from the newest to the oldest")
            else:
                raise _misshapen(number, "a release's heading is ## X.Y.Z (YYYY-MM-DD)")
            found.append((version, day, {part: [] for part in _PARTS.values()}))
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
                "under a release come ### Users and ### Admins, each a list of entries that "
                "start with - and go on indented by two spaces",
            )
    return [
        Release(version, day, tuple(parts["users"]), tuple(parts["admins"]))
        for version, day, parts in found
    ]


def _misshapen(number: int, rule: str) -> ValueError:
    return ValueError(f"Line {number} of CHANGELOG.md is not of the file's shape: {rule}.")


def test_the_changelog_keeps_its_shape():
    text = CHANGELOG.read_text()
    # Strict: a line of another shape raises, naming it.
    releases = parse(text)
    assert releases and len(releases) == len(re.findall(r"^## ", text, re.MULTILINE))
    # Every entry is read, each one line, whatever lines it was wrapped over.
    written = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    for release, section in zip(releases, written, strict=True):
        users = section.partition("### Users\n")[2].partition("### Admins\n")[0]
        assert len(release.users) == len(re.findall(r"^- ", users, re.MULTILINE)), release
    assert all(
        entry and "\n" not in entry and "  " not in entry
        for release in releases
        for entry in release.users + release.admins
    )


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("## 2026-10-21\n", 1),
        ("## v0.1.0 (2026-11-20)\n", 1),
        ("## 0.1.0\n", 1),
        ("## 0.1.0 (20 November 2026)\n", 1),
        ("## 0.1.0 (2026-11-20)\n\n## Unreleased\n", 3),
        ("## Unreleased\n\n## Unreleased\n", 3),
        ("## 0.1.0 (2026-11-20)\n\n### Users\n\n- One\n\n## 0.2.0 (2026-11-21)\n", 7),
        ("## 0.1.0 (2026-11-20)\n\n## 0.1.0 (2026-11-20)\n", 3),
        ("## 0.2.0 (2026-11-20)\n\n## 0.1.0 (2026-11-21)\n", 3),
        ("## Unreleased\n\n- An entry outside a list\n", 3),
        ("## Unreleased\n\n### Users\n\n- One\n   too far in\n", 6),
        ("## Unreleased\n\n### Users\n\n* Another kind of list\n", 5),
        ("## Unreleased\n\nMembers:\n\n- One\n", 3),
        ("## Unreleased\n\n### Operators\n\n- One\n", 3),
    ],
)
def test_a_line_of_another_shape_is_refused_by_its_number(text, line):
    with pytest.raises(ValueError, match=f"^Line {line} of CHANGELOG.md "):
        parse(text)


def test_entries_are_joined_and_releases_read():
    unreleased, newer, older = parse(
        "# Changelog\n\nAn introduction\nof two lines.\n\n## Unreleased\n\n"
        "## 0.10.0 (2026-12-01)\n\n### Users\n\n- One entry\n  over two lines.\n- Another.\n\n"
        "### Admins\n\n- For admins.\n\n"
        "## 0.9.2 (2026-12-01)\n\n### Admins\n\n- Only for admins.\n"
    )
    assert unreleased == Release(None, None, (), ())
    assert newer == Release(
        (0, 10, 0),
        date(2026, 12, 1),
        ("One entry over two lines.", "Another."),
        ("For admins.",),
    )
    assert (older.version, older.day, older.users, older.admins) == (
        (0, 9, 2),
        date(2026, 12, 1),
        (),
        ("Only for admins.",),
    )
