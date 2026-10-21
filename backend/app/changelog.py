"""What's new for members: the Members' entries of CHANGELOG.md, read from the file at the
checkout's root (the image keeps it at /app/CHANGELOG.md, the same place) and served a page of
days at a time (`GET /api/changelog`, app/api/help.py) to the page at /whats-new, which the help
menu marks while a member has entries they have not read.

The file is written for people, and this module holds it to the shape it keeps: `## YYYY-MM-DD`
a day, or `## YYYY-MM-DD to YYYY-MM-DD` for days told as one, newest first, each with `Members:`
and `Operators:` lists of `- ` entries whose lines go on indented by two spaces, a day's newest
entry first. `parse` refuses any other line, and tests/test_changelog.py reads the real file
through it, so an entry written otherwise fails the suite rather than going missing from the
page. The Operators' entries are for the people who run the service and are served to nobody.

What a member has read is the newest day they read and how many entries it held then
(`users.changes_seen_on`, `users.changes_seen_entries`), since a day gains entries with each
release made that day; before they read any, every day up to the one their account was made.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date
from functools import lru_cache
from pathlib import Path

from app.config import ROOT_DIR
from app.models import User

log = logging.getLogger(__name__)

CHANGELOG = ROOT_DIR / "CHANGELOG.md"
# The days a page of what's new holds: a week of releases, or the whole history in two pages.
PAGE_DAYS = 7

_HEADING = re.compile(r"## (\d{4}-\d{2}-\d{2})(?: to (\d{4}-\d{2}-\d{2}))?")
_PARTS = {"Members:": "members", "Operators:": "operators"}
# An entry's line after its first: two spaces, then its words.
_GOES_ON = re.compile(r"  \S.*")


@dataclass(frozen=True)
class Day:
    # The first and the last day the entries were made on, the same for a single day.
    first: date
    last: date
    # Each entry a line of Markdown, the day's newest first.
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


def news() -> tuple[Day, ...]:
    """The days that changed something for members, newest first: CHANGELOG.md's, read again
    when the file changes, as it does in development. None when the file is missing or out of
    shape, logged once, since what's new is no reason for a page to fail."""
    try:
        found = CHANGELOG.stat()
        stamp: tuple[int, int] | None = (found.st_mtime_ns, found.st_size)
    except OSError:
        stamp = None
    return _read(CHANGELOG, stamp)


@lru_cache(maxsize=4)
def _read(path: Path, stamp: tuple[int, int] | None) -> tuple[Day, ...]:
    try:
        days = parse(path.read_text())
    except (OSError, ValueError) as error:
        log.error("What's new shows nothing, since CHANGELOG.md cannot be read: %s", error)
        return ()
    return tuple(day for day in days if day.members)


def page(days: Sequence[Day], before: date | None) -> tuple[list[Day], date | None]:
    """The PAGE_DAYS newest days before `before` (all of them for None), and the day to ask
    before for the next page, None once the oldest is among them."""
    left = [day for day in days if before is None or day.last < before]
    shown = left[:PAGE_DAYS]
    return shown, shown[-1].last if len(left) > PAGE_DAYS else None


def unread(days: Sequence[Day], user: User) -> int:
    """How many entries the user has not read, which are the newest: those of the days after the
    one they read last, and those that day gained since. Before they read any, those of the days
    after their account was made."""
    read, held = user.changes_seen_on, user.changes_seen_entries
    if read is None:
        # The day the account was made is read whole, however many entries it gains.
        read, held = user.created_at.astimezone(UTC).date(), None
    count = 0
    for day in days:
        if day.last < read:
            break
        if day.last == read:
            if held is not None:
                count += max(0, len(day.members) - held)
            break
        count += len(day.members)
    return count


def read_through(days: Sequence[Day], user: User) -> None:
    """Say the user has read every entry: the newest day and how many entries it holds now."""
    if days:
        user.changes_seen_on = days[0].last
        user.changes_seen_entries = len(days[0].members)
