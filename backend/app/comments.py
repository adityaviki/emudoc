"""A document's comments: threads its project's members write in, each about the document as a
whole or a passage of it.

Everyone who sees the project reads and writes them, viewers too, since a comment changes no
document (app/api/comments.py); the writer changes and deletes their own, an owner or admin
deletes anyone's, and anyone who may write in a thread resolves it or opens it again. A thread
started from a passage keeps the words quoted, their page and the version of the document they
were in: a document's passages are written anew at every reading, so the thread's goes null with
the old one (`ON DELETE SET NULL`), while the quote stays to say what it was about. A thread
whose last comment is deleted goes with it.

A comment mentions a member by `<@id>` in its words, which the page writes for a name chosen
after "@" and shows as that name as it is now. Only a member who sees the project may be
mentioned, checked for each mention a comment adds; one an edit keeps is not checked again.
Whoever a comment newly mentions is told (MENTIONED), and the earlier writers of a thread are
told of a reply (THREAD_REPLIED), each once, never the writer, and only while they see the
project. A notice names who wrote and on which document, never the comment's words, which are a
member's and may quote the document; nor does the audit log, which records each comment added
and deleted and each thread resolved or opened again by the document's name and the thread's
id, and which the project's activity reads (app/activity.py). A change to a comment's words is
not recorded, as a note's is not.

The comments are the organisation's, as the document is: they go into the trash with it and out
of it, and with its purge; they outlive their writer's account without its name; they travel in
the project's export (app/export.py), and a member's own are in their account's export. Neither
the agent nor the search reads them.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.access import seeing
from app.mail import mentioned_email, thread_replied_email
from app.models import (
    Block,
    Comment,
    CommentThread,
    Document,
    Membership,
    Passage,
    Project,
    User,
)
from app.notices import MENTIONED, PROJECT_PAGE, THREAD_REPLIED, tell
from app.schemas import (
    QUOTE_LENGTH,
    CommentOut,
    ExportedComment,
    ExportedThread,
    MentionOut,
    ThreadOut,
)

# A member mentioned in a comment's words: `<@12>`.
MENTION = re.compile(r"<@(\d+)>")
# Where a notice of a comment leads: the document, its thread open.
THREAD_PAGE = PROJECT_PAGE + "/documents/{}?thread={}"

BLANK = "Write something in the comment before you send it."
NOT_MENTIONABLE = (
    "Mention only members who see this project: someone mentioned does not. Choose them from "
    'the list that "@" opens.'
)
PASSAGE_ELSEWHERE = (
    "That passage is not in this document. Open the document at the passage and comment there."
)
BLOCK_GONE = (
    "That part of the document's text is no longer there: it was read again meanwhile. Choose "
    "it again and comment."
)
NOT_YOURS = "Only the member who wrote a comment can change it."
NOT_YOURS_TO_DELETE = "Only the member who wrote a comment, or an owner or admin, can delete it."


class CommentRefused(ValueError):
    """A comment the app does not take, in the sentence the member is shown."""


def mentioned(text: str) -> list[int]:
    """The ids of the members the words mention, each once, in the order they first appear."""
    return list(dict.fromkeys(int(found) for found in MENTION.findall(text)))


def mentionable(session: Session, project: Project) -> list[tuple[int, str]]:
    """The members a comment in the project may mention, by name: id and name."""
    return [
        (user_id, name)
        for user_id, name in session.execute(
            select(User.id, User.name)
            .join(Membership, Membership.user_id == User.id)
            .where(seeing(project))
            .order_by(func.lower(User.name), User.id)
        )
    ]


def start_thread(
    session: Session,
    project: Project,
    document: Document,
    user_id: int,
    text: str,
    passage_id: int | None = None,
    block: int | None = None,
    quote: str | None = None,
) -> tuple[CommentThread, Comment]:
    """Start a thread on the document with its first comment, about the passage, the passage
    that holds the block of its text, the words quoted, or none of these for the document as a
    whole; raises CommentRefused. The caller audits, tells and commits."""
    text = _written(session, project, text, [])
    page = None
    passage = None
    if passage_id is not None:
        passage = session.get(Passage, passage_id)
        if passage is None or passage.document_id != document.id:
            raise CommentRefused(PASSAGE_ELSEWHERE)
        page = passage.page_start
    elif block is not None:
        found = session.execute(
            select(Block.text, Block.page).where(
                Block.document_id == document.id, Block.ordinal == block
            )
        ).first()
        if found is None:
            raise CommentRefused(BLOCK_GONE)
        page = found.page
        passage = session.scalar(
            select(Passage)
            .where(
                Passage.document_id == document.id,
                Passage.block_start <= block,
                Passage.block_end > block,
            )
            .order_by(Passage.ordinal)
            .limit(1)
        )
        # A block no passage holds, such as a heading alone, is quoted by its own words.
        if not quote and passage is None:
            quote = found.text
    if quote:
        quote = _excerpt(quote)
    elif passage is not None:
        quote = _excerpt(passage.text)
    thread = CommentThread(
        document_id=document.id,
        passage_id=passage.id if passage is not None else None,
        quote=quote or None,
        page=page,
        version=document.version,
    )
    session.add(thread)
    session.flush()
    comment = Comment(thread_id=thread.id, author_id=user_id, text=text)
    session.add(comment)
    session.flush()
    return thread, comment


def reply(
    session: Session, project: Project, thread: CommentThread, user_id: int, text: str
) -> Comment:
    """Add the member's comment to the thread; raises CommentRefused. The caller audits, tells
    and commits."""
    comment = Comment(
        thread_id=thread.id, author_id=user_id, text=_written(session, project, text, [])
    )
    session.add(comment)
    session.flush()
    return comment


def change_comment(
    session: Session, project: Project, comment: Comment, user_id: int, text: str
) -> list[int]:
    """Change the words of the member's own comment, and return the members the change newly
    mentions; raises CommentRefused. The caller tells and commits."""
    if comment.author_id != user_id:
        raise CommentRefused(NOT_YOURS)
    before = mentioned(comment.text)
    text = _written(session, project, text, before)
    if text != comment.text:
        comment.text = text
        comment.edited_at = datetime.now(UTC)
    return [user_id for user_id in mentioned(text) if user_id not in before]


def delete_comment(session: Session, comment: Comment, user_id: int, manages: bool) -> bool:
    """Delete the comment, the member's own or, for an owner or admin (`manages`), anyone's, and
    its thread with it when it was the last; returns whether the thread went. Raises
    CommentRefused. The caller audits and commits."""
    if comment.author_id != user_id and not manages:
        raise CommentRefused(NOT_YOURS_TO_DELETE)
    thread_id = comment.thread_id
    session.delete(comment)
    session.flush()
    if session.scalar(select(exists().where(Comment.thread_id == thread_id))):
        return False
    session.execute(delete(CommentThread).where(CommentThread.id == thread_id))
    return True


def resolve(thread: CommentThread, user_id: int, resolved: bool) -> bool:
    """Resolve the thread, or open it again; returns whether that changed it. The caller audits
    and commits."""
    if resolved == (thread.resolved_at is not None):
        return False
    thread.resolved_at = datetime.now(UTC) if resolved else None
    thread.resolved_by = user_id if resolved else None
    return True


def threads_of(session: Session, document_id: int) -> list[ThreadOut]:
    """The document's threads, oldest first, each with its comments."""
    threads = session.scalars(
        select(CommentThread)
        .where(CommentThread.document_id == document_id)
        .order_by(CommentThread.id)
    ).all()
    return _out(session, threads)


def thread_out(session: Session, thread: CommentThread) -> ThreadOut:
    """The thread as a member reads it, its resolver's name and its comments read again."""
    session.refresh(thread)
    return _out(session, [thread])[0]


def exported_threads(session: Session, document_ids: list[int]) -> dict[int, list[ExportedThread]]:
    """The threads of these documents as a project's export holds them, by document, each
    mention written as the member's name."""
    threads = session.scalars(
        select(CommentThread)
        .where(CommentThread.document_id.in_(document_ids))
        .order_by(CommentThread.id)
    ).all()
    held: dict[int, list[ExportedThread]] = {}
    for thread in _out(session, threads):
        held.setdefault(thread.document_id, []).append(
            ExportedThread(
                quote=thread.quote,
                page=thread.page,
                version=thread.version,
                created_at=thread.created_at,
                resolved_at=thread.resolved_at,
                resolved_by=thread.resolver,
                comments=[
                    ExportedComment(
                        author=comment.author,
                        text=readable(comment.text, comment.mentions),
                        created_at=comment.created_at,
                        edited_at=comment.edited_at,
                    )
                    for comment in thread.comments
                ],
            )
        )
    return held


def readable(text: str, mentions: list[MentionOut]) -> str:
    """The comment's words with each member it mentions written as "@" and their name, as the page
    shows them, or "@someone" for an account gone."""
    names = {mention.id: mention.name for mention in mentions}
    return MENTION.sub(lambda found: f"@{names.get(int(found[1])) or 'someone'}", text)


def mention_names(session: Session, texts: list[str]) -> dict[int, str]:
    """The names, as they are now, of the members these words mention."""
    ids = {user_id for text in texts for user_id in mentioned(text)}
    if not ids:
        return {}
    return dict(session.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def tell_comment(
    session: Session,
    project: Project,
    document: Document,
    comment: Comment,
    writer: User,
    mentions: list[int],
    replied: bool,
    app_url: str,
) -> None:
    """Tell the members the comment newly mentions, and for a reply the thread's earlier writers,
    each once and never the writer, those who still see the project alone; in the caller's
    session, to commit with the comment."""
    told = {writer.id}
    page = THREAD_PAGE.format(project.id, document.id, comment.thread_id)
    for user in _seeing(session, project, [i for i in mentions if i not in told]):
        _tell(session, project, document, user, writer, page, app_url, MENTIONED)
        told.add(user.id)
    if not replied:
        return
    earlier = session.scalars(
        select(Comment.author_id)
        .where(
            Comment.thread_id == comment.thread_id,
            Comment.id < comment.id,
            Comment.author_id.is_not(None),
        )
        .distinct()
    ).all()
    for user in _seeing(session, project, [i for i in earlier if i not in told]):
        _tell(session, project, document, user, writer, page, app_url, THREAD_REPLIED)


def _tell(
    session: Session,
    project: Project,
    document: Document,
    user: User,
    writer: User,
    page: str,
    app_url: str,
    kind: str,
) -> None:
    organization = project.organization
    template = mentioned_email if kind == MENTIONED else thread_replied_email
    content = template(
        user.name,
        writer.name,
        document.filename,
        project.name,
        organization.name,
        f"{app_url}{page}",
    )
    tell(session, user, organization.id, kind, content, page, app_url)


def _seeing(session: Session, project: Project, ids: list[int]) -> list[User]:
    """The users of these ids who see the project now, by id."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(seeing(project), User.id.in_(ids))
            .order_by(User.id)
        )
    )


def _written(session: Session, project: Project, text: str, kept: list[int]) -> str:
    """The comment's words as they are stored, once each member they newly mention (not among
    `kept`) is found to see the project; raises CommentRefused."""
    # Postgres refuses a NUL in text; the comment's own lines and spaces are the member's.
    text = text.replace("\x00", "").strip()
    if not text:
        raise CommentRefused(BLANK)
    added = [user_id for user_id in mentioned(text) if user_id not in kept]
    if added and len(_seeing(session, project, added)) < len(added):
        raise CommentRefused(NOT_MENTIONABLE)
    return text


def _excerpt(words: str) -> str:
    """The words a thread quotes, on one line, cut short at a word with an ellipsis."""
    line = " ".join(words.split())
    if len(line) <= QUOTE_LENGTH:
        return line
    cut = line[: QUOTE_LENGTH - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"


def _out(session: Session, threads: list[CommentThread]) -> list[ThreadOut]:
    comments: dict[int, list[Comment]] = {thread.id: [] for thread in threads}
    for comment in session.scalars(
        select(Comment).where(Comment.thread_id.in_(list(comments))).order_by(Comment.id)
    ):
        comments[comment.thread_id].append(comment)
    names = mention_names(session, [c.text for kept in comments.values() for c in kept])
    return [
        ThreadOut(
            id=thread.id,
            document_id=thread.document_id,
            passage_id=thread.passage_id,
            quote=thread.quote,
            page=thread.page,
            version=thread.version,
            created_at=thread.created_at,
            resolved_at=thread.resolved_at,
            resolver=thread.resolver,
            comments=[
                CommentOut(
                    id=comment.id,
                    author_id=comment.author_id,
                    author=comment.author,
                    text=comment.text,
                    created_at=comment.created_at,
                    edited_at=comment.edited_at,
                    mentions=[
                        MentionOut(id=user_id, name=names.get(user_id))
                        for user_id in mentioned(comment.text)
                    ],
                )
                for comment in comments[thread.id]
            ],
        )
        for thread in threads
    ]
