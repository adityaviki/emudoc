"""The agent's tools: five that read the project's indexes, and those of the member's own My files
(app/agent/writing.py), offered to an answer that may write there. Every query is scoped to one
project by the server, not the model, to what is out of the project's trash (app/trash.py): nothing
in it is listed, searched, counted or read, and in a chat about a folder or chosen documents to
those (app/scopes.py).

None of these changes anything the project's members share: a file of My files is the asker's
alone, every write of it a version they can go back on (indexing-approach.md, "What the documents
say is data")."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.tools import ToolDefinition
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.embeddings import Embedder
from app.folders import PATH_SEPARATOR, folder_paths
from app.indexing.enrich import parse_iso_date
from app.indexing.pipeline import Services, unfinished_stages
from app.languages import WORDS_AS_WRITTEN, language_name, project_language
from app.models import Document, Fact, Passage, Section
from app.scopes import Scope, readable
from app.search import hybrid_search

# The documents the tools see: those out of the trash. A folder in the trash holds none of them.
LIVE = Document.deleted_at.is_(None)
# Characters of source text one `read` returns before it asks the model to continue.
READ_BUDGET = 12_000
MAX_OUTLINE_SECTIONS = 400

FOLDER_HELP = (
    "Only documents in this folder and its subfolders, given as a path such as Invoices/2024 "
    "(list_documents shows the folders)."
)


@dataclass
class Writing:
    """What an answer that may write its member's My files writes with (app/agent/writing.py):
    the member, the services that keep a file, and the answer's chat, message and try, by which a
    run that starts over or is tried again writes the files it wrote before again, as versions,
    rather than more of them, and a run of a try no longer the answer's writes nothing. The
    message is None for a run that is no answer's (the planted instructions' command)."""

    services: Services
    user_id: int
    chat_id: int | None = None
    message_id: int | None = None
    attempt: int | None = None
    # The files the run made so far, which numbers the next.
    made: int = 0


@dataclass
class ToolContext:
    """What one tool call runs with: a session of its own, opened for the call by the agent
    loop and closed with it, what the chat is about, and the passages the answer has shown the
    model so far, which the calls of one answer share."""

    session: Session
    embedder: Embedder
    project_id: int
    # The folder or documents the chat is about (app/scopes.py), which every tool reads alone;
    # None for the whole project.
    scope: Scope | None = None
    # Passages shown to the model, keyed by id: the only references an answer may cite.
    sources: dict[int, dict[str, Any]] = field(default_factory=dict)
    # The member's My files, where the answer may write them; None where it may not.
    writing: Writing | None = None
    # The file of My files this call wrote, which the step names and opens: its id, name and
    # version.
    wrote: dict[str, Any] | None = None
    _folder_paths: dict[int, str] | None = field(default=None, repr=False)
    _within: list[int] | None = field(default=None, repr=False)
    _language: str | None = field(default=None, repr=False)

    def folder_paths(self) -> dict[int, str]:
        """Folder id → path, read once per tool call."""
        if self._folder_paths is None:
            self._folder_paths = folder_paths(self.session, self.project_id)
        return self._folder_paths

    def language(self) -> str:
        """The language the project's documents without one of their own are searched in, read
        once per tool call (app/languages.py)."""
        if self._language is None:
            self._language = project_language(self.session, self.project_id)
        return self._language

    def within(self) -> list[int] | None:
        """The ids of the documents the chat is about, read once per tool call; None when it is
        about the whole project."""
        if self.scope is None:
            return None
        if self._within is None:
            self._within = readable(self.session, self.project_id, self.scope)
        return self._within

    def scoped(self, column: InstrumentedAttribute[int]) -> list[ColumnElement[bool]]:
        """The condition that holds a query's document `column` to what the chat is about, as a
        list to spread among the others: none for the whole project."""
        within = self.within()
        return [] if within is None else [column.in_(within)]

    @property
    def place(self) -> str:
        """Where a tool looked, for a result that found nothing there."""
        return "in this project" if self.scope is None else "among the documents this chat is about"

    def cite(self, passage: Passage, title: str) -> str:
        self.sources[passage.id] = {
            "id": passage.id,
            "document_id": passage.document_id,
            "document_title": title,
            "context": passage.context,
            "page_start": passage.page_start,
            "page_end": passage.page_end,
            "excerpt": passage.text[:280],
        }
        return f"[P{passage.id}]"


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListDocumentsInput(_Input):
    query: str | None = Field(
        None, description="Words to look for in titles, file names and summaries."
    )
    doc_type: str | None = Field(None, description="Only documents of this type, e.g. invoice.")
    folder: str | None = Field(None, description=FOLDER_HELP)
    limit: int = Field(50, ge=1, le=200, description="How many documents to return.")
    offset: int = Field(0, ge=0, description="Documents to skip, to page through a long list.")


class GetOutlineInput(_Input):
    document_id: int = Field(description="The document's id, as shown by list_documents.")


class SearchInput(_Input):
    query: str = Field(
        description="What to look for: key terms, a phrase, or a question in plain words."
    )
    document_ids: list[int] | None = Field(
        None,
        description="Search only these documents. Omit to search the whole project.",
    )
    folder: str | None = Field(None, description=FOLDER_HELP)
    limit: int = Field(8, ge=1, le=25, description="How many passages to return.")


class QueryFactsInput(_Input):
    names: list[str] | None = Field(None, description="Fact names to match, e.g. total_amount.")
    document_ids: list[int] | None = Field(None, description="Only facts from these documents.")
    folder: str | None = Field(None, description=FOLDER_HELP)
    doc_type: str | None = Field(None, description="Only facts from documents of this type.")
    contains: str | None = Field(None, description="Only facts whose value contains this text.")
    min_number: float | None = Field(None, description="Numeric value at least this.")
    max_number: float | None = Field(None, description="Numeric value at most this.")
    date_from: str | None = Field(None, description="Date value on or after this, YYYY-MM-DD.")
    date_to: str | None = Field(None, description="Date value on or before this, YYYY-MM-DD.")
    limit: int = Field(100, ge=1, le=500, description="How many rows to return.")


class ReadInput(_Input):
    section_id: int | None = Field(
        None,
        description="Read this section and its subsections (ids come from get_outline).",
    )
    passage_id: int | None = Field(
        None, description="Read this passage together with the passages around it."
    )
    document_id: int | None = Field(
        None, description="Read this document from the start, or the pages given below."
    )
    page_from: int | None = Field(None, description="First page to read, with document_id.")
    page_to: int | None = Field(None, description="Last page to read, with document_id.")
    offset: int = Field(0, ge=0, description="Passages to skip, to continue a long read.")


def list_documents(ctx: ToolContext, args: ListDocumentsInput) -> str:
    conditions = [Document.project_id == ctx.project_id, LIVE, *ctx.scoped(Document.id)]
    if args.folder:
        scope = _folder_scope(ctx, args.folder)
        if isinstance(scope, str):
            return scope
        conditions.append(Document.id.in_(scope))
    if args.doc_type:
        conditions.append(func.lower(Document.doc_type) == args.doc_type.strip().lower())
    for word in (args.query or "").split():
        pattern = f"%{word}%"
        conditions.append(
            or_(
                Document.title.ilike(pattern),
                Document.filename.ilike(pattern),
                Document.summary.ilike(pattern),
            )
        )

    total = ctx.session.scalar(select(func.count()).select_from(Document).where(*conditions))
    documents = ctx.session.scalars(
        select(Document)
        .where(*conditions)
        .order_by(Document.id)
        .offset(args.offset)
        .limit(args.limit)
    ).all()
    if not documents:
        return "No documents match." if total == 0 else f"No documents at offset {args.offset}."

    types = ctx.session.execute(
        select(Document.doc_type, func.count())
        .where(
            Document.project_id == ctx.project_id,
            LIVE,
            *ctx.scoped(Document.id),
            Document.doc_type.is_not(None),
        )
        .group_by(Document.doc_type)
        .order_by(func.count().desc())
    ).all()
    lines = [f"Documents {args.offset + 1}-{args.offset + len(documents)} of {total}."]
    if types:
        where = "this chat's documents" if ctx.scope else "this project"
        lines.append(f"Types in {where}: " + ", ".join(f"{name} ({n})" for name, n in types))
    folders = _folder_summary(ctx)
    if folders:
        lines.append(folders)
    for document in documents:
        lines.append("")
        lines.append(_document_line(ctx, document))
        if document.summary:
            lines.append(f"    {document.summary}")
    return "\n".join(lines)


def _folder_summary(ctx: ToolContext) -> str:
    """The project's folders with their document counts, so the model learns the tree; in a
    chat about part of the project, the folders that hold what it is about."""
    paths = ctx.folder_paths()
    if not paths:
        return ""
    counts = dict(
        ctx.session.execute(
            select(Document.folder_id, func.count())
            .where(Document.project_id == ctx.project_id, LIVE, *ctx.scoped(Document.id))
            .group_by(Document.folder_id)
        ).all()
    )
    if ctx.scope is not None:
        paths = {folder_id: path for folder_id, path in paths.items() if folder_id in counts}
    listed = ", ".join(
        f"{path} ({counts.get(folder_id, 0)})"
        for folder_id, path in sorted(paths.items(), key=lambda item: item[1].lower())
    )
    at_root = counts.get(None, 0)
    if not listed:
        return f"At the root: {at_root}."
    return f"Folders (documents in each): {listed}. At the root: {at_root}."


def _folder_scope(ctx: ToolContext, folder: str) -> list[int] | str:
    """The ids of the documents in a folder and its subfolders, or a message when it is unclear."""
    paths = ctx.folder_paths()
    wanted = folder.strip().strip(PATH_SEPARATOR).lower()
    matches = [folder_id for folder_id, path in paths.items() if path.lower() == wanted]
    if not matches:
        # The last part of a path is enough when it names one folder.
        matches = [
            folder_id
            for folder_id, path in paths.items()
            if path.lower().endswith(f"{PATH_SEPARATOR}{wanted}")
        ]
    if not matches:
        known = ", ".join(sorted(paths.values(), key=str.lower)) or "none"
        return f"No folder named {folder!r}. Folders in this project: {known}."
    if len(matches) > 1:
        options = ", ".join(sorted(paths[match] for match in matches))
        return f"{folder!r} could be any of {options}. Give the full path."
    root = paths[matches[0]]
    subtree = [
        folder_id
        for folder_id, path in paths.items()
        if path == root or path.startswith(f"{root}{PATH_SEPARATOR}")
    ]
    return list(
        ctx.session.scalars(
            select(Document.id).where(
                Document.folder_id.in_(subtree), LIVE, *ctx.scoped(Document.id)
            )
        ).all()
    )


def get_outline(ctx: ToolContext, args: GetOutlineInput) -> str:
    document = _document(ctx, args.document_id)
    if document is None:
        return f"No document with id {args.document_id} {ctx.place}."
    sections = ctx.session.scalars(
        select(Section).where(Section.document_id == document.id).order_by(Section.ordinal)
    ).all()
    lines = [_document_line(ctx, document)]
    if document.summary:
        lines.append(f"Summary: {document.summary}")
    lines.append("")
    for section in sections[:MAX_OUTLINE_SECTIONS]:
        summary = f": {section.summary}" if section.summary else ""
        pages = _pages(section.page_start, section.page_end)
        lines.append(
            f"{'  ' * (section.level - 1)}[S{section.id}] {section.title}"
            f"{f' ({pages})' if pages else ''}{summary}"
        )
    if len(sections) > MAX_OUTLINE_SECTIONS:
        lines.append(f"... {len(sections) - MAX_OUTLINE_SECTIONS} more sections not shown.")
    if not sections:
        lines.append("This document has not been indexed yet.")
    return "\n".join(lines)


def search(ctx: ToolContext, args: SearchInput) -> str:
    document_ids = args.document_ids
    if (within := ctx.within()) is not None:
        # An empty list would search the whole project.
        document_ids = [d for d in within if document_ids is None or d in document_ids]
        if not document_ids:
            return "None of those documents is one this chat is about, so none was searched."
    if args.folder:
        scope = _folder_scope(ctx, args.folder)
        if isinstance(scope, str):
            return scope
        document_ids = [d for d in scope if document_ids is None or d in document_ids]
        if not document_ids:
            return f"No documents in folder {args.folder!r} to search."
    result = hybrid_search(
        ctx.session,
        ctx.embedder,
        ctx.project_id,
        args.query,
        args.limit,
        document_ids,
    )
    if not result.hits:
        return f"No passages match {args.query!r}. Try other words, or browse with list_documents."
    mode = "" if result.semantic else " (keyword matching only: the embedding service is down)"
    lines = [f"{len(result.hits)} passages{mode}, best first."]
    passages = {
        passage.id: passage
        for passage in ctx.session.scalars(
            select(Passage).where(Passage.id.in_([hit.passage_id for hit in result.hits]))
        )
    }
    for hit in result.hits:
        ref = ctx.cite(passages[hit.passage_id], hit.document_title)
        pages = _pages(hit.page_start, hit.page_end)
        lines.append("")
        lines.append(f"{ref} doc {hit.document_id} · {hit.context}{f' · {pages}' if pages else ''}")
        lines.append(hit.text)
    return "\n".join(lines)


def query_facts(ctx: ToolContext, args: QueryFactsInput) -> str:
    filters = [Fact.project_id == ctx.project_id]
    if args.names:
        filters.append(Fact.name.in_([name.strip().lower() for name in args.names]))
    if args.document_ids:
        filters.append(Fact.document_id.in_(args.document_ids))
    if args.folder:
        scope = _folder_scope(ctx, args.folder)
        if isinstance(scope, str):
            return scope
        filters.append(Fact.document_id.in_(scope))
    if args.doc_type:
        filters.append(func.lower(Document.doc_type) == args.doc_type.strip().lower())
    if args.contains:
        filters.append(Fact.value.ilike(f"%{args.contains}%"))
    if args.min_number is not None:
        filters.append(Fact.number >= args.min_number)
    if args.max_number is not None:
        filters.append(Fact.number <= args.max_number)
    for bound, compare in (
        (args.date_from, Fact.date.__ge__),
        (args.date_to, Fact.date.__le__),
    ):
        if bound:
            parsed = parse_iso_date(bound)
            if parsed is None:
                return f"{bound!r} is not a date in YYYY-MM-DD form."
            filters.append(compare(parsed))

    if len(filters) == 1:
        return _fact_names(ctx)
    filters.extend([LIVE, *ctx.scoped(Fact.document_id)])

    joined = select(Fact, Document).join(Document, Document.id == Fact.document_id)
    total = ctx.session.scalar(
        select(func.count())
        .select_from(Fact)
        .join(Document, Document.id == Fact.document_id)
        .where(and_(*filters))
    )
    if not total:
        return "No facts match. Call query_facts with no arguments to see the fact names available."
    rows = ctx.session.execute(
        joined.where(and_(*filters))
        .order_by(Fact.document_id, Fact.name, Fact.id)
        .limit(args.limit)
    ).all()

    passage_ids = [fact.passage_id for fact, _ in rows if fact.passage_id]
    passages = {
        passage.id: passage
        for passage in ctx.session.scalars(select(Passage).where(Passage.id.in_(passage_ids)))
    }
    lines = [
        f"{total} facts match"
        + (f", showing the first {len(rows)}." if total > len(rows) else "."),
        "",
        "name | value | document | page | source",
    ]
    for fact, document in rows:
        value = worded_value(fact)
        passage = passages.get(fact.passage_id)
        source = ctx.cite(passage, document.display_title) if passage else "-"
        lines.append(
            f"{fact.name} | {value} | doc {document.id} · {document.display_title} | "
            f"{_pages(fact.page, fact.page) or '-'} | {source}"
        )

    totals = ctx.session.execute(
        select(
            Fact.name,
            Fact.currency,
            func.count(),
            func.sum(Fact.number),
            func.min(Fact.number),
            func.max(Fact.number),
        )
        .join(Document, Document.id == Fact.document_id)
        .where(and_(*filters), Fact.number.is_not(None))
        .group_by(Fact.name, Fact.currency)
        .order_by(Fact.name)
    ).all()
    if totals:
        lines.append("")
        lines.append("Numeric values over all matching facts:")
        for name, currency, count, total_value, low, high in totals:
            unit = f" {currency}" if currency else ""
            lines.append(
                f"{name}{unit}: count {count}, sum {total_value:,.2f}, "
                f"min {low:,.2f}, max {high:,.2f}"
            )
    return "\n".join(lines)


def read(ctx: ToolContext, args: ReadInput) -> str:
    query = (
        select(Passage)
        .join(Document, Document.id == Passage.document_id)
        .where(Passage.project_id == ctx.project_id, LIVE, *ctx.scoped(Passage.document_id))
    )
    if args.section_id is not None:
        section = ctx.session.scalar(
            select(Section)
            .join(Document, Document.id == Section.document_id)
            .where(
                Section.id == args.section_id,
                Section.project_id == ctx.project_id,
                LIVE,
                *ctx.scoped(Section.document_id),
            )
        )
        if section is None:
            return f"No section with id {args.section_id} {ctx.place}."
        query = query.where(
            Passage.document_id == section.document_id,
            Passage.block_start >= section.block_start,
            Passage.block_start < section.block_end,
        )
    elif args.passage_id is not None:
        center = ctx.session.scalar(query.where(Passage.id == args.passage_id))
        if center is None:
            return f"No passage with id {args.passage_id} {ctx.place}."
        query = query.where(
            Passage.document_id == center.document_id,
            Passage.ordinal.between(center.ordinal - 2, center.ordinal + 2),
        )
    elif args.document_id is not None:
        query = query.where(Passage.document_id == args.document_id)
        if args.page_from is not None:
            query = query.where(Passage.page_end >= args.page_from)
        if args.page_to is not None:
            query = query.where(Passage.page_start <= args.page_to)
    else:
        return "Give a section_id, a passage_id or a document_id."

    passages = ctx.session.scalars(query.order_by(Passage.ordinal).offset(args.offset)).all()
    if not passages:
        return "Nothing to read there." if args.offset == 0 else "No more passages."

    document = ctx.session.get(Document, passages[0].document_id)
    lines = [_document_line(ctx, document)]
    used = 0
    last_context = None
    for index, passage in enumerate(passages):
        if index and used + len(passage.text) > READ_BUDGET:
            lines.append("")
            lines.append(
                f"... {len(passages) - index} more passages. Call read again with "
                f"offset={args.offset + index} to continue."
            )
            break
        if passage.context != last_context:
            lines.append("")
            lines.append(f"## {passage.context}")
            last_context = passage.context
        ref = ctx.cite(passage, document.display_title)
        pages = _pages(passage.page_start, passage.page_end)
        lines.append("")
        lines.append(f"{ref}{f' {pages}' if pages else ''}")
        lines.append(passage.text)
        used += len(passage.text)
    return "\n".join(lines)


def _fact_names(ctx: ToolContext) -> str:
    names = ctx.session.execute(
        select(Fact.name, func.count(), func.count(func.distinct(Fact.document_id)))
        .join(Document, Document.id == Fact.document_id)
        .where(Fact.project_id == ctx.project_id, LIVE, *ctx.scoped(Fact.document_id))
        .group_by(Fact.name)
        .order_by(func.count().desc(), Fact.name)
    ).all()
    if not names:
        return "No facts have been extracted in this project yet."
    lines = ["Fact names in this project (facts, documents):"]
    lines.extend(
        f"{name}: {facts} facts in {documents} documents" for name, facts, documents in names
    )
    lines.append("")
    lines.append("Call query_facts with names or other filters to get the values.")
    return "\n".join(lines)


def _document(ctx: ToolContext, document_id: int) -> Document | None:
    return ctx.session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == ctx.project_id,
            LIVE,
            *ctx.scoped(Document.id),
        )
    )


def _document_line(ctx: ToolContext, document: Document) -> str:
    parts = [f"doc {document.id} · {document.display_title}"]
    if document.title and document.title != document.filename:
        parts.append(f"file {document.filename}")
    folder_paths = ctx.folder_paths()
    if document.folder_id is not None and document.folder_id in folder_paths:
        parts.append(f"folder {folder_paths[document.folder_id]}")
    if document.doc_type:
        parts.append(document.doc_type)
    if document.doc_date:
        parts.append(f"dated {document.doc_date.isoformat()}")
    # So that the model searches a document in another language by its own words: keyword
    # search stems a query in each language the project holds, but finds no translation.
    if document.language == WORDS_AS_WRITTEN and ctx.language() != WORDS_AS_WRITTEN:
        parts.append("searched by its words as written")
    elif document.language and document.language != ctx.language():
        parts.append(f"written in {language_name(document.language)}")
    if document.page_count:
        parts.append(f"{document.page_count} pages")
    if document.status == "quarantined":
        parts.append("quarantined by the malware scan, nothing of it can be read")
    elif document.status != "ready":
        parts.append(f"indexing {document.status}")
    elif unfinished := unfinished_stages(document):
        # So that the model looks for what it lacks by searching and reading it instead.
        parts.append(f"partly indexed, {', '.join(_WITHOUT[name] for name in unfinished)}")
    return " · ".join(parts)


# What a partly indexed document lacks for the model, by the stage it went without.
_WITHOUT = {"enrich": "no summaries or facts", "embed": "found by keyword search alone"}


def worded_value(fact: Fact) -> str:
    """A fact's value as the model is told it, with its currency and how far it can be trusted:
    checked or corrected by a member, or a quote the source does not hold."""
    value = fact.value + (f" ({fact.currency})" if fact.currency else "")
    if fact.check is not None and fact.check.corrected:
        # A member's correction is the fact's value; the source passage holds what the model
        # misread.
        return value + f" (corrected by a member; the indexing model read {fact.check.read_value})"
    if fact.check is not None:
        return value + " (checked by a member)"
    if not fact.verified:
        return value + " (unverified: quote not found in the source)"
    return value


def _pages(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    return f"p. {start}" if end is None or end == start else f"pp. {start}-{end}"


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[_Input]
    run: Callable[[ToolContext, Any], str]
    # What the chat's step keeps of a call's input, where not all of it: a file's whole text is
    # the file's, not the chat's, which the project's members may read once it is shared.
    step: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def stepped(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return arguments if self.step is None else self.step(arguments)

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters_json_schema=_schema(self.input_model),
        )


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()

    def strip_titles(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: strip_titles(value) for key, value in node.items() if key != "title"}
        if isinstance(node, list):
            return [strip_titles(item) for item in node]
        return node

    return strip_titles(schema)


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "list_documents",
            "List the project's documents with their folder, type, date, length and summary, "
            "and the project's folders. Call this to see what the project contains, to find a "
            "document by name or kind, or to enumerate every document of a type or in a folder "
            "before checking each one.",
            ListDocumentsInput,
            list_documents,
        ),
        Tool(
            "get_outline",
            "Show one document's outline: its sections with ids, page ranges and one-sentence "
            "summaries. Call this to find where in a long document something is discussed "
            "before reading that section.",
            GetOutlineInput,
            get_outline,
        ),
        Tool(
            "search",
            "Search the passages of the project's documents by keywords and meaning. Returns "
            "the best-matching passages in full, each with a [P...] reference to cite. Call "
            "this to find a specific detail. It returns only the top matches, so do not use it "
            "to count or to check that something holds for every document.",
            SearchInput,
            search,
        ),
        Tool(
            "query_facts",
            "Query the fact table: typed fields extracted from every document, such as dates, "
            "parties, amounts and identifiers, each linked to its source passage. Returns all "
            "matching facts, plus counts and sums for numeric ones; a fact the project's members "
            "checked or corrected says so, and a correction is the value to use. Call this for "
            "questions "
            "that filter, compare, total or must cover every document. Call it with no "
            "arguments to list the fact names that exist.",
            QueryFactsInput,
            query_facts,
        ),
        Tool(
            "read",
            "Read exact source text: a section with its subsections, the passages around one "
            "passage, or a page range of a document. Returns passages with [P...] references "
            "to cite. Call this when a search hit needs its surrounding text, or to read a "
            "section found in an outline.",
            ReadInput,
            read,
        ),
    )
}
