"""A project's filing rules: where its new documents go, and the tags they are given, once each
has been read for its type and facts.

A rule (`FilingRule`) has a condition (the document's type, a fact read from it whose value
holds some words, or any document) and an action (move it into a folder, file it below a folder
in a folder of its year, or of its year and month, or tag it). The rules apply in the order they
were made: the first rule that moves and whose condition the document meets moves it, and every
rule that tags and whose condition it meets tags it.

A document is filed once: as the pipeline makes it ready (`file_if_due`, from `_make_ready` in
app/indexing/pipeline.py, in the same transaction, so that whoever sees it ready sees it where it
was filed), after a run that read its type and facts, while it has no `filed_at`, which filing
then sets whether a rule matched or not. A document read without its summaries and facts waits
for them, and is filed when "Index the rest" reads them; the documents there were before rules
existed were marked filed by their migration, so a rule made later files only what arrives after
it.

Only a document in the project's root is moved: one uploaded into a folder, or sent to a project
whose address files into one, was put there by whoever sent it; and a document of a connected
folder (`ConnectorFile`) is never moved, its sync keeping it where its folder's copy is. Tags are
put on wherever the document is. A move lands under a name no live sibling has
(`unique_filename`), and a dated rule makes the year's and the month's folders where none of
their name is (`folder_at`). The app's moves are not audited, as a member's are not; making,
changing and deleting the rules is (app/api/filing.py).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.folders import folder_at, unique_filename
from app.models import ConnectorFile, Document, Fact, FilingRule, Folder, Project
from app.tags import add_tag

log = logging.getLogger(__name__)

# How many filing rules a project may have: each is read for every document indexed.
MAX_FILING_RULES = 20

TOO_MANY = (
    f"A project has at most {MAX_FILING_RULES} filing rules. Delete one before making another."
)
FOLDER_TRASHED = (
    "Its folder is in the trash, so it files nothing there. Restore the folder, or choose another."
)


def project_rules(session: Session, project_id: int) -> list[FilingRule]:
    """The project's rules in the order they apply: the order they were made."""
    return list(
        session.scalars(
            select(FilingRule).where(FilingRule.project_id == project_id).order_by(FilingRule.id)
        )
    )


def problem_of(rule: FilingRule, folder: Folder | None) -> str | None:
    """Why the rule would file nothing as its project stands now; None when it files as it
    says. A folder or tag deleted for good took the rule with it."""
    if folder is not None and folder.deleted_at is not None:
        return FOLDER_TRASHED
    return None


def file_if_due(session: Session, document: Document) -> None:
    """File the document by its project's rules if this is the run that read its type and facts
    and it has not been filed; the caller is about to make it ready and commits. Its row is read
    again under a lock first, so that a member's move or rename while it was being read holds,
    and is not written back over by what this session read before."""
    if document.filed_at is not None or document.stages.get("enrich", {}).get("status") != "done":
        return
    session.refresh(document, with_for_update=True)
    project = session.get(Project, document.project_id)
    if document.filed_at is None and project.deleted_at is None and not project.sample:
        file_document(session, document)


def file_document(session: Session, document: Document) -> None:
    """Apply the rules of the document's project to it and mark it filed; the caller holds the
    document's row and commits."""
    rules = [rule for rule in project_rules(session, document.project_id) if rule.enabled]
    if rules:
        facts = _facts(session, document.id)
        movable = document.folder_id is None and not _connected(session, document.id)
        for rule in rules:
            if not _meets(rule, document, facts):
                continue
            if rule.action == "tag":
                add_tag(document, rule.tag_id)
            elif movable and (target := _target(session, rule, document)) is not None:
                # Named while it is still in the root, so that its own name is not taken.
                document.filename = unique_filename(
                    session, document.project_id, target, document.filename
                )
                document.folder_id = target
                movable = False
    document.filed_at = datetime.now(UTC)


def _meets(rule: FilingRule, document: Document, facts: list[tuple[str, str]]) -> bool:
    if rule.condition == "type":
        return document.doc_type is not None and document.doc_type == rule.value.lower()
    if rule.condition == "fact":
        field, words = rule.field.lower(), rule.value.lower()
        return any(name.lower() == field and words in value.lower() for name, value in facts)
    return True


def _target(session: Session, rule: FilingRule, document: Document) -> int | None:
    """The folder the rule moves the document into, made where a dated one needs it; None when
    it moves it nowhere: its folder is in the trash, or the document has no date to file by."""
    folder = session.get(Folder, rule.folder_id) if rule.folder_id is not None else None
    if folder is not None and folder.deleted_at is not None:
        return None
    if rule.action == "folder":
        return folder.id if folder is not None else None
    if document.doc_date is None:
        return None
    names = [f"{document.doc_date.year}"]
    if rule.action == "by_month":
        names.append(f"{document.doc_date.month:02d}")
    return folder_at(session, document.project_id, folder.id if folder else None, names)


def _facts(session: Session, document_id: int) -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in session.execute(
            select(Fact.name, Fact.value).where(Fact.document_id == document_id)
        )
    ]


def _connected(session: Session, document_id: int) -> bool:
    found = select(func.count()).where(ConnectorFile.document_id == document_id)
    return bool(session.scalar(found))
