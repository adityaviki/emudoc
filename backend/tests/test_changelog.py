"""What's new (app/changelog.py, app/api/help.py): the members' entries of CHANGELOG.md, served a
page of days at a time to anyone, and how far each member read them, which the help menu marks.
The real file is read here through the parser, so that an entry written in another shape fails
the suite rather than going missing from the page."""

import logging
import re
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

import app.changelog
from app.changelog import CHANGELOG, PAGE_DAYS, news, parse
from app.models import User
from tests.test_auth import signup

OPERATORS_ONLY = "Only for the people who run the service."


def written(monkeypatch, tmp_path, text: str) -> None:
    """Serve `text` as the changelog, from a file of its own, so that no earlier read of
    another is kept for it."""
    path = tmp_path / f"CHANGELOG-{uuid4().hex}.md"
    path.write_text(text)
    monkeypatch.setattr(app.changelog, "CHANGELOG", path)


def changelog(*days: tuple[str, list[str]]) -> str:
    """A changelog of days, each a heading and its members' entries, every day with an entry
    for operators too."""
    parts = ["# Changelog\n\nWhat changed, newest first.\n"]
    for heading, entries in days:
        members = "".join(f"- {entry}\n" for entry in entries)
        parts.append(
            f"## {heading}\n\n"
            + (f"Members:\n\n{members}\n" if entries else "")
            + f"Operators:\n\n- {OPERATORS_ONLY}\n"
        )
    return "\n".join(parts)


def made_on(session, day: str) -> None:
    """Every account as if made at noon of `day`, UTC."""
    for user in session.scalars(select(User)):
        user.created_at = datetime.fromisoformat(f"{day}T12:00:00+00:00")
    session.commit()


def unread(client) -> int:
    return client.get("/api/auth/me").json()["changes_unread"]


def test_the_changelog_is_of_the_shape_the_page_reads():
    text = CHANGELOG.read_text()
    # Strict: a line of another shape raises, naming it.
    days = parse(text)
    assert len(days) == len(re.findall(r"^## ", text, re.MULTILINE))
    assert [day.last for day in days] == sorted((day.last for day in days), reverse=True)
    # Every entry for members is read, each one line, whatever lines it was wrapped over.
    for day, written_day in zip(days, re.split(r"^## ", text, flags=re.MULTILINE)[1:], strict=True):
        members = written_day.partition("Members:\n")[2].partition("Operators:\n")[0]
        assert len(day.members) == len(re.findall(r"^- ", members, re.MULTILINE)), day.last
    assert all(
        entry and "\n" not in entry and "  " not in entry for day in days for entry in day.members
    )
    # What the page is served: every day that changed something for members.
    assert news() == tuple(day for day in days if day.members) != ()


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("## 21 October 2026\n", 1),
        ("## 2026-10-20\n\nMembers:\n\n- One\n\n## 2026-10-21\n", 7),
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


def test_entries_are_joined_and_spans_of_days_read(monkeypatch, tmp_path):
    written(
        monkeypatch,
        tmp_path,
        "# Changelog\n\nAn introduction\nof two lines.\n\n"
        "## 2026-10-11\n\nMembers:\n\n- Organisations and `roles`,\n  with the sign-in pages.\n"
        "- The library is a tree.\n\nOperators:\n\n- Migrations.\n\n"
        "## 2026-10-05 to 2026-10-10\n\nMembers:\n\n- Documents are indexed.\n",
    )
    first, span = news()
    assert first.members == (
        "Organisations and `roles`, with the sign-in pages.",
        "The library is a tree.",
    )
    assert first.operators == ("Migrations.",)
    assert (span.first, span.last) == (date(2026, 10, 5), date(2026, 10, 10))


@pytest.mark.parametrize("text", [None, "## Yesterday\n"])
def test_a_missing_or_misshapen_changelog_shows_nothing_and_says_so(
    client, monkeypatch, tmp_path, caplog, text
):
    path = tmp_path / f"CHANGELOG-{uuid4().hex}.md"
    if text is not None:
        path.write_text(text)
    monkeypatch.setattr(app.changelog, "CHANGELOG", path)
    with caplog.at_level(logging.ERROR, logger="app.changelog"):
        assert client.get("/api/changelog").json() == {"days": [], "next_before": None}
        assert unread(client) == 0
    # Once, however often it is asked.
    (record,) = caplog.records
    assert record.getMessage().startswith("What's new shows nothing, since CHANGELOG.md cannot")


def test_what_is_new_is_served_to_anyone_a_page_of_days_at_a_time(
    client, monkeypatch, tmp_path, signed_in_only
):
    days = [(f"2026-10-{day:02}", [f"Entry {day}a", f"Entry {day}b"]) for day in range(21, 11, -1)]
    # A day that changed nothing for members is not shown.
    days[3] = (days[3][0], [])
    written(monkeypatch, tmp_path, changelog(*days))

    first = client.get("/api/changelog")
    assert first.status_code == 200
    found = first.json()
    assert [day["last"] for day in found["days"]] == [
        "2026-10-21",
        "2026-10-20",
        "2026-10-19",
        "2026-10-17",
        "2026-10-16",
        "2026-10-15",
        "2026-10-14",
    ]
    assert len(found["days"]) == PAGE_DAYS
    assert found["days"][0] == {
        "first": "2026-10-21",
        "last": "2026-10-21",
        "entries": ["Entry 21a", "Entry 21b"],
    }
    assert found["next_before"] == "2026-10-14"
    rest = client.get("/api/changelog", params={"before": found["next_before"]}).json()
    assert [day["last"] for day in rest["days"]] == ["2026-10-13", "2026-10-12"]
    assert rest["next_before"] is None
    # The operators' entries are theirs.
    assert OPERATORS_ONLY not in first.text + str(rest)


def test_a_member_is_told_what_is_new_until_they_read_it(client, session, monkeypatch, tmp_path):
    signup(client)
    made_on(session, "2026-10-12")
    written(
        monkeypatch,
        tmp_path,
        changelog(("2026-10-13", ["Undo"]), ("2026-10-12", ["Trash"]), ("2026-10-11", ["Tree"])),
    )
    # The days after the account was made; the day it was made counts as read.
    assert unread(client) == 1

    # A release the same day adds its entry first.
    written(
        monkeypatch,
        tmp_path,
        changelog(("2026-10-13", ["Bell", "Undo"]), ("2026-10-12", ["Trash"])),
    )
    assert unread(client) == 2
    read = client.post("/api/auth/changelog/read")
    assert read.status_code == 200, read.text
    assert read.json()["changes_unread"] == 0
    user = session.scalars(select(User).where(User.email == "ada@example.com")).one()
    session.refresh(user)
    assert (user.changes_seen_on, user.changes_seen_entries) == (date(2026, 10, 13), 2)

    # The day gains an entry, and a day comes after it.
    written(
        monkeypatch,
        tmp_path,
        changelog(("2026-10-13", ["Help", "Bell", "Undo"]), ("2026-10-12", ["Trash"])),
    )
    assert unread(client) == 1
    written(
        monkeypatch,
        tmp_path,
        changelog(
            ("2026-10-14", ["Flags", "Support"]),
            ("2026-10-13", ["Help", "Bell", "Undo"]),
        ),
    )
    assert unread(client) == 3
    assert client.post("/api/auth/changelog/read").json()["changes_unread"] == 0
    # Days told as one later, with fewer entries, leave nothing unread.
    written(monkeypatch, tmp_path, changelog(("2026-10-13 to 2026-10-14", ["All of it"])))
    assert unread(client) == 0

    # The account's own, so in its export.
    exported = client.get("/api/auth/account/export").json()["account"]
    assert (exported["changes_seen_on"], exported["changes_seen_entries"]) == ("2026-10-14", 2)


def test_an_account_made_today_has_nothing_to_read(client, monkeypatch, tmp_path):
    today = datetime.now(UTC).date().isoformat()
    written(monkeypatch, tmp_path, changelog((today, ["Something new"])))
    assert signup(client)["changes_unread"] == 0


def test_only_an_account_says_it_read(client, signed_in_only):
    assert client.post("/api/auth/changelog/read").status_code == 401
