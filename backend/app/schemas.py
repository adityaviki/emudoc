import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

from app.costs import costs_shown
from app.question_sets import MAX_SET_QUESTIONS, QUESTION_CHARS
from app.selection import MAX_CHOSEN


class _FromORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# PROJECT_VISIBILITIES in app/models.py, which the database's check constraint holds.
ProjectVisibility = Literal["organization", "private"]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


class NewProjectIn(ProjectIn):
    """A project to make: a private one is seen by its maker alone at first, and the owners and
    admins, until its maker chooses others."""

    visibility: ProjectVisibility = "organization"


class ProjectOut(_FromORM):
    id: int
    name: str
    description: str
    created_at: dt.datetime
    visibility: str
    # The sample project a new organisation starts with (app/sample), which takes no uploads.
    sample: bool = False
    document_count: int = 0
    # The documents that are partly indexed (`unfinished_stages`, app/indexing/pipeline.py),
    # which the library offers to index the rest of, and those of them without summaries and
    # facts, which wait for the model as well as for the embeddings endpoint.
    partly_indexed_count: int = 0
    unenriched_count: int = 0


class TrashedProjectOut(BaseModel):
    """A project in the organisation's trash (app/trash.py), with what it holds."""

    id: int
    name: str
    sample: bool
    document_count: int
    deleted_at: dt.datetime
    # Who deleted it; null once their account is gone.
    deleted_by: str | None
    # When the housekeeper deletes it for good, unless it is restored first.
    purged_at: dt.datetime


class TrashOut(BaseModel):
    """The organisation's projects in the trash that the account sees, the latest deleted
    first, and how many days the trash keeps what is put in it."""

    projects: list[TrashedProjectOut]
    days: int


class TrashedOut(BaseModel):
    """Something in a project's trash: a folder, with the documents that went with it, a
    document deleted by itself, or one of the asker's own chats."""

    kind: Literal["folder", "document", "chat"]
    id: int
    # The folder's or the file's name, or the chat's title (empty before its first question).
    name: str
    # The path of the folder it was in; null at the project's root, and for a chat.
    place: str | None
    documents: int
    mime_type: str | None
    deleted_at: dt.datetime
    deleted_by: str | None
    purged_at: dt.datetime


class ProjectTrashOut(BaseModel):
    """The newest of what a project's trash holds for the asker, newest first, how much it holds
    in all, and how many days it keeps it."""

    items: list[TrashedOut]
    total: int
    days: int


class ProjectAccessIn(BaseModel):
    """Who is to see a project besides the organisation's owners and admins: everyone in it, or
    the members chosen. An open project keeps no members, so `user_ids` counts only for a
    private one."""

    visibility: ProjectVisibility
    user_ids: list[int] = Field(default_factory=list, max_length=1000)


class ProjectAccessOut(BaseModel):
    """Who sees a project besides the organisation's owners and admins: everyone in it, or the
    members chosen, by user id."""

    visibility: str
    user_ids: list[int]


# FACT_TYPES in app/models.py: what a fact's value is, and so a template's field.
FactType = Literal["text", "number", "date", "money"]

# A template's ceilings (app/extraction.py). Every template in force is listed in the enrich
# stage's prompt, so these bound what reading each of the project's documents costs.
MAX_TEMPLATE_FIELDS = 30
MAX_FIELD_EXAMPLES = 3
MAX_TEMPLATE_WORDS = 300
MAX_EXAMPLE_CHARS = 100

_Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TEMPLATE_WORDS)]


class TemplateFieldIn(BaseModel):
    """A field of an extraction template: its name, which its facts are named by, snake_case
    once the server has made it so; its type; what it is; whether every document of the kind
    should state it; and values as documents write them, which show the model what to find."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    type: FactType
    description: _Line = ""
    required: bool = False
    examples: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_EXAMPLE_CHARS),
        ]
    ] = Field(default_factory=list, max_length=MAX_FIELD_EXAMPLES)


class TemplateIn(BaseModel):
    """A project's extraction template as a member writes it, whole: the kind of document it is
    for (lowercase words once the server has made them so), its name, what the kind is, and its
    fields in the order they are asked for."""

    doc_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: _Line = ""
    fields: list[TemplateFieldIn] = Field(min_length=1, max_length=MAX_TEMPLATE_FIELDS)


class TemplateFieldOut(BaseModel):
    name: str
    type: FactType
    description: str
    required: bool
    examples: list[str]


class TemplateOut(BaseModel):
    """An extraction template in force for a project: its own (`id` set) or a built-in one
    (`built_in`, `id` null), which applies while the project has none of its own kind."""

    id: int | None
    doc_type: str
    title: str
    description: str
    fields: list[TemplateFieldOut]
    built_in: bool
    # A project's own template of a kind a built-in one is for, which it replaces.
    replaces_built_in: bool
    # The project's documents whose facts were last read for it.
    documents: int
    # Those of its kind read for no template, such as the ones read before it was made, which
    # reading its documents again takes too.
    matching: int
    # Its documents and those waiting for the enrich stage, read again or retried.
    reading: int
    # Those whose last reading of its kind changed a field or the template they are read for.
    changed: int
    updated_at: dt.datetime | None


_Kind = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ReadAgainIn(BaseModel):
    """Which of a project's documents to read again for its templates as they are now: those of
    the kind a template is for (`kind`: read for it, and of its kind read for none), and those
    read for a kind no template is for any more, as before a template's kind was changed or it
    was deleted (`was`)."""

    kind: _Kind | None = None
    was: _Kind | None = None


class ReadAgainOut(BaseModel):
    queued: int


class FieldChangeOut(BaseModel):
    """A field whose facts a document's last reading changed (app/fact_changes.py): the values
    read before and no longer, and those read now and not before, each list cut at a few of
    the counts; `checked` when a value gone was a member's check, which the new reading
    disagrees with."""

    name: str
    before: list[str]
    after: list[str]
    before_count: int
    after_count: int
    checked: bool


class DocumentChangesOut(BaseModel):
    """What a document's last reading changed: the template it was read for before and now
    (null for none), and its fields."""

    document_id: int
    title: str
    filename: str
    read_at: dt.datetime
    template_before: str | None
    template_after: str | None
    fields: list[FieldChangeOut]


class FactChangesOut(BaseModel):
    """The last readings of a project's documents of one kind, before or now: the template in
    force for the kind (null for none), how many of its documents wait to be read, how many
    were last read and how many of those changed something, how many disagree with a member's
    check, and a page of those that changed, those first, then the newest."""

    kind: str
    template: TemplateOut | None
    reading: int
    read: int
    changed: int
    unheld: int
    documents: list[DocumentChangesOut]
    next_after: str | None


class ProjectTemplatesOut(BaseModel):
    """The templates in force for a project: its own by title, then the built-in ones of the
    kinds it has none of; and how many of its own it may have."""

    templates: list[TemplateOut]
    max_templates: int


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None


class FolderPatch(BaseModel):
    """Rename and/or move a folder. A field that is left out is left unchanged."""

    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: int | None = None


class FolderOut(_FromORM):
    id: int
    project_id: int
    parent_id: int | None
    name: str
    created_at: dt.datetime
    # The documents directly in the folder, not in its subfolders: the library lists them a
    # page at a time, so the count is what says whether the folder has anything to open.
    document_count: int = 0


class UsageOut(BaseModel):
    """What one kind of work used for a document or an answer, summed over every run for it:
    the counts by unit, the model when one was called, and the cost in millionths of the
    currency the status names, when the model has a price and the reader is an owner or an
    admin (app/costs.py); a member or a viewer reads null."""

    model: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    pages: int = 0
    bytes: int = 0
    inputs: int = 0
    cost_micros: int | None = None

    @field_serializer("cost_micros")
    def _withheld(self, cost: int | None) -> int | None:
        return cost if costs_shown() else None


class StageOut(BaseModel):
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"
    detail: str = ""
    # While a failed stage waits to run again by itself (app/worker.py): when, and whether for
    # the day's spending limit to reset rather than after a backoff. None once it runs or
    # "Index again" overtakes it, and for a wait from before the field.
    retry_at: dt.datetime | None = None
    deferred: bool = False


class DocumentPatch(BaseModel):
    """Rename and/or move a document. A field that is left out is left unchanged."""

    filename: str | None = Field(None, min_length=1, max_length=500)
    folder_id: int | None = None


# REVIEW_STATES in app/models.py.
ReviewState = Literal["to_review", "approved", "disputed"]
# DOCUMENT_DETAILS in app/models.py.
DocumentDetail = Literal["title", "doc_type", "doc_date", "language"]


class DocumentDetailsIn(BaseModel):
    """A member's title, kind, date and language for a document (app/details.py). A field left
    out stays as it is; one given, None for none, is the member's, which later readings keep; one
    named in `as_read` takes what the reading found again, and follows the readings after. A
    language is one of the organisation's languages (`GET /api/organization/languages`), None
    for the organisation's own."""

    title: str | None = Field(None, max_length=500)
    doc_type: str | None = Field(None, max_length=100)
    doc_date: dt.date | None = None
    language: str | None = Field(None, pattern=r"^[a-z_]{1,63}$")
    as_read: list[DocumentDetail] = Field([], max_length=4)


class DocumentNoteIn(BaseModel):
    """A document's note as a member saves it, whole (app/notes.py); a blank one deletes it."""

    text: str = Field(max_length=10_000)


class DocumentNoteOut(BaseModel):
    """A document's note, "" for none, with the name of whoever changed it last as it is now
    (None for an account gone, and for no note) and when."""

    text: str
    changed_by: str | None
    changed_at: dt.datetime | None


class DocumentCopyOut(BaseModel):
    """A document of the same bytes as another, in another project the member sees
    (app/duplicates.py), with where it is."""

    id: int
    project_id: int
    project_name: str
    # Its folder's path from the project's root, "" for the root, and the folder's id.
    folder: str
    folder_id: int | None
    filename: str


class DocumentCopiesOut(BaseModel):
    """The copies of a document's file in the other projects the member sees, by project name."""

    copies: list[DocumentCopyOut]


class DuplicateOut(BaseModel):
    """A file held in more than one of the projects the member sees: every copy of it, by project
    name, and its size and type."""

    size_bytes: int
    mime_type: str
    copies: list[DocumentCopyOut]


class DuplicatesOut(BaseModel):
    """A page of the files held in more than one of the projects the member sees, the one most
    lately added first, with how many there are and how many bytes their copies past the first
    take, over every page; `next` the `after` of the page after it, None on the last."""

    files: int
    copy_bytes: int
    duplicates: list[DuplicateOut]
    next: int | None


class DocumentOut(_FromORM):
    id: int
    project_id: int
    folder_id: int | None
    filename: str
    mime_type: str
    size_bytes: int
    # `quarantined` is a file the malware scan flagged: `error` says so, and the file is
    # neither indexed nor served (app/scanning.py).
    status: Literal["queued", "processing", "ready", "failed", "quarantined"]
    stages: dict[str, StageOut]
    error: str | None
    page_count: int | None
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    # The text search configuration its words are searched in, None for its organisation's
    # (app/languages.py).
    language: str | None = None
    # What its last reading found of the four above, and which of them a member set in its
    # place (app/details.py).
    read_title: str | None = None
    read_doc_type: str | None = None
    read_doc_date: dt.date | None = None
    read_language: str | None = None
    edited_details: list[DocumentDetail] = []
    summary: str | None
    # The kind of the extraction template its facts were read for (app/extraction.py), if any.
    template: str | None
    # By kind: `upload` (bytes, pages), `ocr`, `infer_sections`, `enrich`, `embed`, `render`;
    # its current version's (app/versions.py).
    usage: dict[str, UsageOut]
    created_at: dt.datetime
    # Which version of the document its file is, from 1 (app/versions.py).
    version: int
    # Its view, the PDF of a Word, PowerPoint, Excel, RTF or HTML file that shows it as it looks
    # (app/conversion.py): being made, made, or failed with the sentence why; None for none.
    view_state: Literal["queued", "ready", "failed"] | None = None
    view_error: str | None = None
    # Its review (app/reviews.py), None for none: the member it is assigned to, by id and their
    # name as it is now, and who moved it to its state and when, None for an automation or an
    # account gone.
    review: ReviewState | None
    assignee_id: int | None
    assignee: str | None
    review_changer: str | None
    review_changed_at: dt.datetime | None


class DocumentVersionOut(BaseModel):
    """A version of a document (app/versions.py): its current file, or an earlier one kept to open
    and download."""

    number: int
    current: bool
    filename: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    uploaded_at: dt.datetime
    # The name of the member who brought it in, as it is now; None for nobody's (a document
    # from before uploads were kept) or an account gone.
    uploaded_by: str | None
    # Whether its file is served: the malware scan passed it, as `download_file` asks.
    served: bool
    # Whether its text was read, which an earlier version shows (`…/versions/{n}/content`).
    has_text: bool


class ReviewIn(BaseModel):
    """A change to a document's review (app/reviews.py); a field left out stays as it is. A state
    of None takes it out of review, and its assignee with it; an assignee given to a document in
    no review puts it in review."""

    state: ReviewState | None = None
    assignee_id: int | None = None


class ReviewerOut(BaseModel):
    """A member a project's documents may be assigned to."""

    id: int
    name: str


class ReviewsOut(BaseModel):
    """A page of a project's documents in review of one state, those assigned to the asker alone
    when asked, with how many there are of each state, so asked, over every page."""

    documents: list[DocumentOut]
    counts: dict[ReviewState, int]
    # What to pass as `after` for the next page; None on the last one.
    next_after: str | None


class DocumentPage(BaseModel):
    """A page of the documents the library lists in one place (directly in a folder, of a tag,
    the reader's favourites or the documents they opened lately), in the order asked for: by
    name (the `natural_order` collation in app/models.py) unless another is."""

    documents: list[DocumentOut]
    # What to pass as `after` for the next page; None on the last one.
    next_after: str | None


# The orders the library lists documents in (`SORTS` in app/api/documents.py): by name, as they
# were added, by the documents' own dates (the undated after the dated either way), by size.
DocumentSort = Literal["name", "added", "date", "size"]
SortOrder = Literal["asc", "desc"]


class ChosenDocumentsIn(BaseModel):
    """Documents a member chose together in the library, by id (app/selection.py)."""

    document_ids: list[int] = Field(min_length=1, max_length=MAX_CHOSEN)


# The stage a reprocess run starts from; later stages run too, and so does an earlier one
# that is not done, which is how a quarantined document is scanned again.
Stage = Literal["scan", "parse", "structure", "enrich", "embed"]


class DocumentsReprocessIn(ChosenDocumentsIn):
    """Index the chosen documents again from `from_stage`; without one, those of them that are
    partly indexed, each from the first stage it went without ("Index the rest")."""

    from_stage: Stage | None = None


class DocumentsReprocessOut(BaseModel):
    # The chosen documents queued, as they stand now.
    documents: list[DocumentOut]
    # How many of the chosen were being indexed already, and were left as they are.
    skipped: int


_TypeName = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)
]
# Words a search is for, or that a document mentions: as many as a member types in the box.
_Words = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
# How many searches a search may be within, each the documents the one before found.
MAX_MENTIONS = 5


class DocumentFilter(BaseModel):
    """What a search searches within (app/api/query.py, app/document_filter.py), each part
    narrowing the others: the documents of one of its types, as the model names a document's
    kind; dated on or after `after` and on or before `before`, which leaves out the undated; in
    one of its review states; in its folder or below it; with a passage holding every word of
    each of its `mentions`. A part left out matches every document."""

    types: list[_TypeName] = Field(default_factory=list, max_length=20)
    after: dt.date | None = None
    before: dt.date | None = None
    reviews: list[ReviewState] = Field(default_factory=list, max_length=3)
    folder_id: int | None = None
    mentions: list[_Words] = Field(default_factory=list, max_length=MAX_MENTIONS)


class FavoritesOut(BaseModel):
    """The documents of a project the reader starred, by id, the newest star first."""

    document_ids: list[int]


class DocumentTypeOut(BaseModel):
    """A type the model gave a project's documents, and how many of them out of the trash."""

    type: str
    count: int


class RejectedFile(BaseModel):
    filename: str
    reason: str
    # The file's place among those the request carried, from 0, so that a program tells apart
    # two files of one name; the documents kept are the others, in their order.
    index: int | None = None
    # The document of the project that holds the same bytes, in its library or its trash, when
    # that is why: a file sent again is there already rather than refused.
    duplicate_of: int | None = None


class UploadResult(BaseModel):
    documents: list[DocumentOut]
    rejected: list[RejectedFile]
    # The upload the documents kept are, which the page follows as they are indexed (GET
    # /api/projects/{id}/uploads); None when the request kept none.
    upload_id: int | None = None


class UploadFailureOut(BaseModel):
    """A document of an upload that could not be indexed, and why, in the words its tab shows."""

    id: int
    filename: str
    reason: str
    # Set apart by the malware scan rather than failed (app/scanning.py).
    quarantined: bool


class UploadStatusOut(BaseModel):
    """How far the documents of some uploads are indexed, those in the trash left out. Each is
    counted once: ready (partly indexed or not), indexing, waiting for a retry of a stage that
    failed for a reason that passes, or failed; so `documents` is the sum of the four."""

    documents: int
    ready: int
    # Of the ready, those without summaries and facts or their vectors, while no retry waits.
    partly_indexed: int
    indexing: int
    retrying: int
    # When the soonest retry is due; None while none waits.
    retry_at: dt.datetime | None
    # Failed or set apart by the malware scan.
    failed: int
    # The first of them by name, at most LISTED; `failed` counts them all.
    failures: list[UploadFailureOut]


class SectionOut(BaseModel):
    id: int
    title: str
    level: int
    summary: str | None
    page_start: int | None
    page_end: int | None
    block_start: int
    block_end: int
    children: list["SectionOut"] = []


class BlockOut(_FromORM):
    ordinal: int
    kind: str
    level: int | None
    text: str
    page: int | None
    bbox: list[float] | None


class DocumentContent(BaseModel):
    blocks: list[BlockOut]
    # [width, height] per page in the same units as block bboxes; empty when there are no pages.
    page_sizes: list[list[float]]
    # The same for the pages of its view, a Word, PowerPoint or Excel file shown as it looks
    # (app/conversion.py), whose blocks have no place on them; empty until it is made.
    view_page_sizes: list[list[float]] = []


class PassageOut(BaseModel):
    id: int
    document_id: int
    document_title: str
    section_id: int
    context: str
    text: str
    page_start: int | None
    page_end: int | None
    block_start: int
    block_end: int


class FactCheckOut(BaseModel):
    """A member's check of a fact (app/fact_checks.py): who and when, and with a correction what
    the model read in its place."""

    id: int
    # The member's name as it is now; None once their account is gone.
    checked_by: str | None
    checked_at: dt.datetime
    # The model's value, when the member wrote another; None for a check of the model's own.
    corrected_from: str | None
    # What the member said the field holds: their value, or the model's they checked.
    value: str


class FactCheckIn(BaseModel):
    """A fact checked as it reads now (no `value`), or corrected to the value the document states:
    words for a text, digits for a number or an amount (with its currency, else the fact's), a
    date as 2026-03-01."""

    value: str | None = Field(default=None, max_length=4000)
    currency: str | None = Field(default=None, max_length=10)


class FactOut(BaseModel):
    id: int
    document_id: int
    document_title: str
    name: str
    value_type: str
    value: str
    number: float | None
    date: dt.date | None
    currency: str | None
    quote: str | None
    page: int | None
    block_ordinal: int | None
    passage_id: int | None
    # Whether the model's quote was found in the text; a person's word is `check`.
    verified: bool
    check: FactCheckOut | None


class FactList(BaseModel):
    total: int
    facts: list[FactOut]


class FactRowOut(BaseModel):
    """A row of the facts table (app/fact_table.py): a document, and the facts of each of the
    template's fields it has, in the document's order; a field it has none of is left out."""

    document_id: int
    title: str
    filename: str
    cells: dict[str, list[FactOut]]
    # The checks of a field no fact reads any more, by the field's name: made before the
    # document was read again, which read it differently.
    earlier: dict[str, list[FactCheckOut]]
    # The document's review (app/reviews.py), None for none, and the member it is assigned to, by
    # id and name.
    review: ReviewState | None = None
    assignee_id: int | None = None
    assignee: str | None = None


class FactTableOut(BaseModel):
    """A page of the facts table of one kind of document: the template whose fields are its
    columns, the rows, how many rows the conditions keep, and where the next page starts."""

    template: TemplateOut
    rows: list[FactRowOut]
    total: int
    # How many of those rows hold a fact of the fields no member has checked.
    unchecked: int
    next_after: str | None


CrossCheckPairing = Literal["same", "key", "every"]
CrossCheckComparison = Literal["equal", "at_most", "at_least", "same_words", "same_numbers"]
FindingOutcome = Literal["differs", "missing", "holds"]
# Why a finding is what it is (app/cross_checks.py, OUTCOME_OF).
FindingReason = Literal[
    "holds",
    "differs",
    "currency",
    "no_value",
    "no_key",
    "no_partner",
    "no_partner_value",
    "no_item",
    "untyped",
]
_FieldName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class CrossCheckIn(BaseModel):
    """A cross-check as a member writes it, whole (app/cross_checks.py): its name; the kind and
    field of the documents checked and the kind and field they are compared with; how documents
    are paired (`same`, `key` with the field of each side whose values pair them, `every`);
    whether line by line; and the comparison, its margin (days between dates, else in the values'
    units) and whether the paired documents' values are added up first."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    left_kind: _Kind
    left_field: _FieldName
    right_kind: _Kind
    right_field: _FieldName
    pairing: CrossCheckPairing
    left_key: _FieldName | None = None
    right_key: _FieldName | None = None
    by_item: bool = False
    comparison: CrossCheckComparison
    margin: float = Field(default=0, ge=0, le=1e12)
    summed: bool = False


class CrossCheckSuggestionOut(CrossCheckIn):
    """A rule offered to the project, which a member adds as it is, with the types of the fields
    it compares."""

    left_type: FactType
    right_type: FactType


class FindingCountsOut(BaseModel):
    differs: int
    missing: int
    holds: int


class CrossCheckOut(CrossCheckIn):
    id: int
    # The types of the fields compared, as the project's templates hold them now, which the page
    # words the rule by; None for a field gone.
    left_type: FactType | None
    right_type: FactType | None
    # Why the rule cannot be worked out with the project's templates as they are now, such as a
    # field taken out of one since; None when it can.
    problem: str | None
    # What it finds now, by outcome; None while it has a problem.
    counts: FindingCountsOut | None
    updated_at: dt.datetime


class CrossChecksOut(BaseModel):
    """A project's cross-checks, by name, the rules offered that it has none of, and how many it
    may have."""

    checks: list[CrossCheckOut]
    suggestions: list[CrossCheckSuggestionOut]
    max_checks: int


class PartnerOut(BaseModel):
    document_id: int
    title: str


class FindingOut(BaseModel):
    """What a cross-check found for a value of one of the documents it checks: the document, the
    outcome and why, the value checked and those it was compared with, each with where it was
    found, the documents paired with it, and how far apart the two were: the checked value less
    the other (or their sum), in days for dates."""

    document_id: int
    title: str
    filename: str
    outcome: FindingOutcome
    reason: FindingReason
    left: FactOut | None
    right: list[FactOut]
    partners: list[PartnerOut]
    difference: float | None


class FindingsOut(BaseModel):
    """A page of a cross-check's findings, those of one outcome or all, differing first: how many
    the outcome holds, and where the next page starts."""

    check: CrossCheckOut
    outcome: FindingOutcome | None
    findings: list[FindingOut]
    total: int
    next_after: str | None


class FactName(BaseModel):
    name: str
    count: int


class SnippetSegment(BaseModel):
    text: str
    match: bool


class SearchHitOut(BaseModel):
    passage_id: int
    document_id: int
    document_title: str
    doc_type: str | None
    context: str
    snippet: list[SnippetSegment]
    page_start: int | None
    page_end: int | None
    score: float
    keyword_rank: int | None
    vector_rank: int | None


class SearchOut(BaseModel):
    hits: list[SearchHitOut]
    semantic: bool


class SearchIn(DocumentFilter):
    """The library's search, as its query string asks it: the words, how many passages at most,
    and what it searches within, one document and the documents the filter matches (each of
    its lists a parameter given once a value, `tags=1&tags=2`)."""

    q: str
    limit: int = Field(10, ge=1, le=50)
    document_id: int | None = None


# How many projects a search across them may be narrowed to: more is most of them.
MAX_SEARCHED_PROJECTS = 100


class SearchAllIn(BaseModel):
    """The search of every project the member sees (app/api/search.py), as its query string asks
    it: the words, how many passages at most, and what narrows it, each part narrowing the
    others: some of those projects, the documents of one of its types, dated on or after `after`
    and on or before `before`, which leaves out the undated. A part left out takes everything."""

    q: str
    limit: int = Field(10, ge=1, le=50)
    projects: list[int] = Field(default_factory=list, max_length=MAX_SEARCHED_PROJECTS)
    types: list[_TypeName] = Field(default_factory=list, max_length=20)
    after: dt.date | None = None
    before: dt.date | None = None


class SearchAllHitOut(SearchHitOut):
    """A passage the search across projects found, with the project it is in, by its name now."""

    project_id: int
    project_name: str


class SearchAllOut(BaseModel):
    hits: list[SearchAllHitOut]
    semantic: bool


ChatVisibility = Literal["private", "project"]
# What a chat is about (app/scopes.py), and how many documents it may be about: more is a
# folder's work.
ChatScopeKind = Literal["project", "folder", "documents"]
SCOPE_DOCUMENTS = 20


class ChatScopeIn(BaseModel):
    """What a chat is about: one of the project's folders with its subfolders, or documents
    chosen from it; neither is the whole project."""

    folder_id: int | None = None
    document_ids: list[int] = Field([], max_length=SCOPE_DOCUMENTS)


class ScopeFolderOut(BaseModel):
    id: int
    name: str


class ScopeDocumentOut(BaseModel):
    """A document a chat is about, by the name the library shows it by."""

    id: int
    filename: str


class ChatScopeOut(BaseModel):
    """What a chat is about, its folder or its documents those of them out of the trash: a
    folder deleted is none, and documents deleted are left out."""

    kind: ChatScopeKind
    folder: ScopeFolderOut | None
    documents: list[ScopeDocumentOut]


class ChatOut(_FromORM):
    id: int
    project_id: int
    # Who started the chat, and their name: a shared chat is read by the project's other
    # members, but asked in, renamed and deleted by its starter alone.
    user_id: int
    started_by: str
    title: str
    visibility: ChatVisibility
    scope: ChatScopeOut
    # Whether the reader pinned it to the top of their chat list: each reader's own.
    pinned: bool = False
    created_at: dt.datetime
    updated_at: dt.datetime


class ChatIn(BaseModel):
    """A new chat: about the whole project unless `scope` says otherwise."""

    scope: ChatScopeIn | None = None


class ChatPatch(BaseModel):
    """Rename and/or share a chat, or change what it is about (an empty scope is the whole
    project). A field that is left out is left unchanged."""

    title: str | None = Field(None, min_length=1, max_length=200)
    visibility: ChatVisibility | None = None
    scope: ChatScopeIn | None = None


class QuestionIn(BaseModel):
    content: str = Field(min_length=1)


class RegenerateIn(BaseModel):
    """The chat's last question as its member edited it, or none to answer it again as it is."""

    question: str | None = Field(None, min_length=1)


class StepFile(BaseModel):
    """A file of the asker's My files that a step wrote (app/agent/writing.py), which it opens."""

    id: int
    name: str
    version: int


class ChatStep(BaseModel):
    """One tool call the agent made while answering."""

    id: str
    name: str
    input: dict[str, Any]
    summary: str = ""
    is_error: bool = False
    file: StepFile | None = None


class SourceOut(BaseModel):
    """A passage the agent was shown, which its answer may cite as [P<id>]."""

    id: int
    document_id: int
    document_title: str
    context: str
    page_start: int | None
    page_end: int | None
    excerpt: str


# How far an answer is. While it is `streaming` the row is empty and its events, streamed by
# the events route, are the answer so far; a question is always `done`.
MessageStatus = Literal["streaming", "done", "stopped", "failed"]


class ChatMessageOut(_FromORM):
    id: int
    role: Literal["user", "assistant"]
    content: str
    steps: list[ChatStep]
    sources: list[SourceOut]
    notice: str | None
    error: str | None
    status: MessageStatus
    # What the answer used, under `answer`, once its run has recorded it; empty before that
    # and for a question.
    usage: dict[str, UsageOut]
    created_at: dt.datetime


class ChatDetail(ChatOut):
    messages: list[ChatMessageOut]


class AskOut(BaseModel):
    """A question as saved, and the empty answer whose events the client follows."""

    question: ChatMessageOut
    answer: ChatMessageOut


class RegenerateOut(AskOut):
    """The chat's last question, edited or not, and its answer emptied and streaming again, with
    the id of the last event the earlier tries left, as `RetryOut` has it."""

    last_event_id: int | None


class RetryOut(BaseModel):
    """An answer tried again, emptied and streaming, and the id of the last event its earlier
    tries left: sent as `Last-Event-ID`, it follows the new try's events alone, the first of
    them `restart`. Null when there are none, and the new try's are followed from the start."""

    answer: ChatMessageOut
    last_event_id: int | None


# How far a run of cross-checks is (app/check_runs.py), and its discrepancy report.
RunStatus = Literal["queued", "running", "done", "failed"]
ReportStatus = Literal["queued", "writing", "done", "failed"]


class CheckRunIn(BaseModel):
    """A run a member asks for: the project's cross-checks by id, or every one of them when
    null, and whether the agent writes the discrepancy report once they are worked out."""

    check_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    report: bool = False


class CheckRunRuleOut(CrossCheckIn):
    """A rule of a run as it was when the run was made: the cross-check's id (which may be gone
    since), what it compares and the types of its fields, and once the run is worked out, why it
    could not be or what it found by outcome."""

    check_id: int
    left_type: FactType | None
    right_type: FactType | None
    problem: str | None
    counts: FindingCountsOut | None


class RunTriggerOut(BaseModel):
    """What an automation's run was made for: the automation, by its id while it is there and
    its name as it was, and how many documents arrived, those it watches and those its checks
    compare them with, the first few by their names."""

    automation_id: int | None
    automation: str
    documents: int
    partners: int
    names: list[str]


class CheckRunSummaryOut(BaseModel):
    """A run as the project's list shows it: when, by whom (their name as it is now, None once
    their account is gone, or for an automation's run, `trigger` saying which and why), how far
    it and its report are, its rules' names and what they found together."""

    id: int
    status: RunStatus
    report_status: ReportStatus | None
    run_by: str | None
    trigger: RunTriggerOut | None
    titles: list[str]
    counts: FindingCountsOut | None
    created_at: dt.datetime
    finished_at: dt.datetime | None


class CheckRunOut(CheckRunSummaryOut):
    """A run whole: its rules, why it failed, and its report, which cites the passages it was
    shown as [P<id>], with what the report used."""

    project_id: int
    user_id: int | None
    error: str | None
    rules: list[CheckRunRuleOut]
    report: str
    report_sources: list[SourceOut]
    report_notice: str | None
    report_error: str | None
    usage: dict[str, UsageOut]


AutomationAudience = Literal["maker", "project", "nobody"]
# AUTOMATION_REVIEWS in app/models.py.
AutomationReview = Literal["differs", "arrived"]


class AutomationIn(BaseModel):
    """An automation as a member writes it, whole (app/automations.py): its name; the kind of
    document it watches (as templates name kinds), or any; the folder it watches, its subfolders
    with it, or the whole project; the cross-checks it runs by id, or every one of the project's
    when null; whether the model writes the discrepancy report; whom it tells what a run found;
    whether documents its checks compare the watched ones with run it again; whether it is on;
    and what its run puts in review (app/reviews.py), none when null, and whom it assigns them to,
    only with a review."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    kind: _Kind | None = None
    folder_id: int | None = None
    check_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    report: bool = False
    audience: AutomationAudience = "maker"
    recheck: bool = False
    enabled: bool = True
    review: AutomationReview | None = None
    assignee_id: int | None = None


class AutomationOut(AutomationIn):
    id: int
    # The cross-checks it chose that are still there, which may be none; None when it runs
    # every one.
    check_ids: list[int] | None
    # The folder's path as it is now, from the project's root, to word it by; None for the
    # whole project.
    folder: str | None
    # The names of the cross-checks it chose, by name; None when it runs every one.
    checks: list[str] | None
    # Who made it, whom `maker` tells, by their id and their name as it is now; None once their
    # account is gone.
    created_by: int | None
    made_by: str | None
    # The name of the member it assigns what it puts in review to, as it is now.
    assignee: str | None
    # Why it would run nothing or tell nobody as the project stands now; None when it runs as
    # it says.
    problem: str | None
    # When it last made a run, of those kept.
    last_run_at: dt.datetime | None
    updated_at: dt.datetime


class AutomationsOut(BaseModel):
    """A project's automations, by name, and how many it may have."""

    automations: list[AutomationOut]
    max_automations: int


# QUESTION_SET_SCHEDULES and REPORT_TRIGGERS in app/models.py.
QuestionSetSchedule = Literal["daily", "weekly"]
ReportTrigger = Literal["schedule", "arrivals", "member"]
_SavedQuestion = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUESTION_CHARS)
]


class QuestionSetIn(BaseModel):
    """A question set as a member writes it, whole (app/question_sets.py): its name; its
    questions, in the order they are asked; its schedule, every day or one day a week (`weekday`,
    Monday 0), at `hour` o'clock of its maker's zone, or none; whether documents arriving in the
    project run it; whom the reports its schedule and its arrivals make are told to; and whether
    it is on. Something runs it: a schedule, arrivals, or both."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    questions: list[_SavedQuestion] = Field(min_length=1, max_length=MAX_SET_QUESTIONS)
    schedule: QuestionSetSchedule | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int = Field(default=8, ge=0, le=23)
    on_arrivals: bool = False
    audience: AutomationAudience = "maker"
    enabled: bool = True


class QuestionSetOut(QuestionSetIn):
    id: int
    # The zone its hour is in, its maker's, "UTC" while they chose none or once they are gone.
    time_zone: str
    # When its schedule runs it next; None without one or while it is off.
    next_run_at: dt.datetime | None
    # Who made it, by their id and their name as it is now; None once their account is gone.
    created_by: int | None
    made_by: str | None
    # Why it would run nothing or tell nobody as the organisation stands now; None when it runs
    # as it says.
    problem: str | None
    # When it last made a report, of those kept, and whether one is waiting or being answered.
    last_report_at: dt.datetime | None
    running: bool
    updated_at: dt.datetime


class QuestionSetsOut(BaseModel):
    """A project's question sets, by name, how many it may have and how many questions a set
    may ask."""

    question_sets: list[QuestionSetOut]
    max_question_sets: int
    max_questions: int


class ReportAnswerOut(BaseModel):
    """One question of a report, as it was asked, and its answer, which cites the passages it
    was shown as [P<id>]. `status` is `queued` until it is asked and `running` while it is; a failed
    one says why in `error` (the day's spending used up, the agent's run failed); `notice` is what
    the answer adds about itself, such as that it was cut off."""

    question: str
    status: RunStatus
    content: str
    sources: list[SourceOut]
    notice: str | None
    error: str | None


class QuestionReportSummaryOut(BaseModel):
    """A report as the project's list shows it: which set, by its id while it is there and its
    name as it was; what ran it (its schedule, documents that arrived, or a member, by their name
    as it is now, None once their account is gone); how far it is; how many of its questions
    were answered and failed; and how many documents arrived for it, the first few by name."""

    id: int
    question_set_id: int | None
    title: str
    trigger: ReportTrigger
    run_by: str | None
    status: RunStatus
    questions: int
    answered: int
    failed: int
    arrived: int
    names: list[str]
    created_at: dt.datetime
    finished_at: dt.datetime | None


class QuestionReportOut(QuestionReportSummaryOut):
    """A report whole: every question with its answer, why it failed, and what its answers
    used."""

    project_id: int
    user_id: int | None
    error: str | None
    answers: list[ReportAnswerOut]
    usage: dict[str, UsageOut]


class QuestionReportsOut(BaseModel):
    """A page of a project's reports, the newest first, and the id to ask before for the next."""

    reports: list[QuestionReportSummaryOut]
    next_before: int | None


class MailboxIn(BaseModel):
    """A project's address as its members set it (app/inbound.py): whom it takes mail from
    besides themselves, each an address or a domain, and the folder what arrives goes into, the
    project's root when null."""

    senders: list[Annotated[str, StringConstraints(max_length=320)]] = Field(
        default_factory=list, max_length=200
    )
    folder_id: int | None = None


class InboundRejectedOut(BaseModel):
    filename: str
    reason: str


class InboundMessageOut(BaseModel):
    """A message sent to the project's address and what became of it: the documents kept, the
    files that were not, or why it was turned away (`reason`)."""

    id: int
    received_at: dt.datetime
    sender: str
    # The member whose address sent it, by their name as it is now; None for anyone else.
    member: str | None
    subject: str
    outcome: Literal["kept", "refused", "empty"]
    reason: str
    kept: int
    rejected: list[InboundRejectedOut]


class MailboxOut(BaseModel):
    """A project's address, or that it has none (`address` null), with the messages it received
    lately, newest first; `available` is whether the deployment receives mail at all."""

    available: bool
    address: str | None
    senders: list[str]
    folder_id: int | None
    # The folder's path as it is now; None for the project's root, which a folder gone or in the
    # trash falls back to.
    folder: str | None
    max_senders: int
    # How long a message's record is kept.
    days: int
    messages: list[InboundMessageOut]


class ConnectorServiceOut(BaseModel):
    """A service the deployment can connect folders from."""

    provider: str
    label: str


class ConnectorFileOut(BaseModel):
    """A file of a connected folder that is no document of the project, by its path below the
    folder, and why."""

    path: str
    reason: str


class ConnectorOut(BaseModel):
    """A folder of a member's Google Drive, Dropbox or OneDrive that the project keeps in step
    with (app/connectors/): whose it is, how it stands, and what its last sync left out."""

    id: int
    provider: str
    label: str
    # The account at the service it was connected from, and the member who connected it, by
    # their name as it is now.
    account: str
    member: str
    # Whether it is the reader's own: only its member chooses its folder or connects it again.
    mine: bool
    status: Literal["choosing", "active", "paused"]
    # The folder's name at the service; None while it is being chosen.
    folder: str | None
    target_folder_id: int | None
    # Where its files go, the folder's path as it is now; None for the project's root.
    target: str | None
    # Why the last sync did not finish, or why it is paused; empty when all went well.
    problem: str
    synced_at: dt.datetime | None
    # When it is synced next, while it is synced at all.
    next_sync_at: dt.datetime | None
    # Whether a sync is queued or running now.
    syncing: bool
    # The documents of its files in the library.
    kept: int
    # The files that are no document, the first SHOWN of them by path, and how many in all.
    rejected: list[ConnectorFileOut]
    rejected_count: int


class ConnectorsOut(BaseModel):
    """The project's connected folders, and the services a member may connect another from."""

    services: list[ConnectorServiceOut]
    connectors: list[ConnectorOut]
    max_connectors: int
    # How often each is synced.
    interval_minutes: int


class ConnectIn(BaseModel):
    """Connect a service to the project, or connect a paused connector of the member's again
    (`connector_id`)."""

    provider: Annotated[str, StringConstraints(max_length=20)]
    connector_id: int | None = None


class ConsentOut(BaseModel):
    """Where to send the browser for the member's consent."""

    url: str


class ConnectorIn(BaseModel):
    """The folder at the service a connector keeps the project in step with, chosen once, while
    it is being chosen; and where its files go in the project, its root when null."""

    folder_ref: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    target_folder_id: int | None = None


class RemoteFolderOut(BaseModel):
    """A folder at the service; `choosable` is False for a place browsing passes through."""

    ref: str
    name: str
    choosable: bool


class RemoteFoldersOut(BaseModel):
    folders: list[RemoteFolderOut]


class CheckRunsOut(BaseModel):
    """A page of the project's runs, newest first, and the id the next page starts before."""

    runs: list[CheckRunSummaryOut]
    next_before: int | None


class RunFindingsOut(BaseModel):
    """A page of what one rule of a run found, as it was found, those of one outcome or all,
    differing first; and of the documents the page names, those deleted or in the trash since,
    which cannot be opened."""

    rule: CheckRunRuleOut
    outcome: FindingOutcome | None
    findings: list[FindingOut]
    total: int
    next_after: int | None
    gone: list[int]


# The kinds of document the agent writes to a template of the project's (app/draft_templates.py).
DraftKind = Literal["reply_email", "dispute_letter", "summary_memo", "comparison_table", "report"]
# The characters of what the agent writes into a Word document (app/agent/writing.py), and the
# documents a writing outside a chat may be about (app/writings.py).
DRAFT_CHARS = 100_000
DRAFT_DOCUMENTS = 10


# A writing template's ceilings (app/draft_templates.py), which bound what it adds to every
# writing's prompt: its parts, and the characters of its tone, of a part's name and of what goes
# in it, and of the rest it asks.
MAX_DRAFT_SECTIONS = 20
TONE_CHARS = 500
HEADING_CHARS = 100
SECTION_CHARS = 500
GUIDANCE_CHARS = 2000


class DraftSectionIn(BaseModel):
    """A part of a kind's documents: its name, what goes in it, and whether every one has it or
    only one whose documents give it something to say."""

    heading: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=HEADING_CHARS)
    ]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=SECTION_CHARS)
    ] = ""
    required: bool = True


class DraftTemplateIn(BaseModel):
    """A project's own template of a kind the agent writes, written whole: its tone, its parts in
    order and what else every document of the kind should do."""

    tone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=TONE_CHARS)] = ""
    sections: list[DraftSectionIn] = Field(default_factory=list, max_length=MAX_DRAFT_SECTIONS)
    guidance: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=GUIDANCE_CHARS)
    ] = ""


class DraftSectionOut(BaseModel):
    heading: str
    description: str
    required: bool


class DraftTemplateOut(BaseModel):
    """The template in force for a kind the agent writes for the project: its own (`id`, and when
    it was last changed) or the built-in one."""

    kind: DraftKind
    id: int | None
    built_in: bool
    tone: str
    sections: list[DraftSectionOut]
    guidance: str
    updated_at: dt.datetime | None


class DraftTemplatesOut(BaseModel):
    """The template in force for each kind, in the order the kinds are offered."""

    templates: list[DraftTemplateOut]


# MY_FILE_KINDS and MY_FILE_SCANS in app/models.py.
MyFileKind = Literal["docx", "xlsx"]
MyFileScan = Literal["pending", "passed", "skipped", "flagged"]


class MyFileVersionOut(_FromORM):
    """A version of a file in My files (app/my_files.py): who made it (`by_model` for the agent's
    writing), what changed, its malware scan, and its view, whose pages the page draws."""

    number: int
    size_bytes: int
    by_model: bool
    note: str
    scan: MyFileScan
    view_state: Literal["queued", "ready", "failed"] | None
    view_page_sizes: list[list[float]]
    view_error: str | None
    created_at: dt.datetime


class MyFileOut(BaseModel):
    """A file of the member's My files as their list shows it: its name and kind, the document of
    the library it was copied from while that is there to see, its newest version and how many
    it has."""

    id: int
    project_id: int
    name: str
    kind: MyFileKind
    origin_document_id: int | None
    origin_name: str | None
    latest: MyFileVersionOut
    versions: int
    created_at: dt.datetime
    updated_at: dt.datetime


class MyFileDetailOut(MyFileOut):
    """A file with every version, the newest first."""

    history: list[MyFileVersionOut]


# WRITING_STATUSES in app/models.py.
WritingStatus = Literal["queued", "writing", "failed"]


class MyFileWritingIn(BaseModel):
    """A document of a kind a member asks the agent to write into their My files, about the
    documents named (app/writings.py)."""

    kind: DraftKind
    document_ids: list[int] = Field(min_length=1, max_length=DRAFT_DOCUMENTS)


class MyFileWritingOut(_FromORM):
    """A writing of the member's as their My files list it while it is under way or once it
    failed, with why."""

    id: int
    kind: DraftKind
    title: str
    status: WritingStatus
    error: str | None
    created_at: dt.datetime


class MyFilesOut(BaseModel):
    """The member's files in the project, the one changed last first, how many it keeps, and the
    writings they asked for that are under way or failed, the newest first."""

    files: list[MyFileOut]
    limit: int
    writings: list[MyFileWritingOut] = Field(default_factory=list)


class MyFileRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)


class MyFileCopyIn(BaseModel):
    """Where a file goes in the project's library: a new document in `folder_id` (the root for
    none), or with `as_version` the next version of the document it was copied from."""

    folder_id: int | None = None
    as_version: bool = False


# Enough to catch a typo; the verification email settles whether the address is real.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# NIST SP 800-63B: a minimum length and no composition rules; the routes refuse a breached
# password (app/passwords.py).
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200

Email = Annotated[str, Field(max_length=320, pattern=EMAIL_PATTERN)]
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
# An IANA zone such as Europe/Berlin; which names are known is the route's to check.
TimeZone = Annotated[str, Field(max_length=64)]


class SetupIn(BaseModel):
    """What the setup page makes the install's first account and its workspace with
    (app/signups.py)."""

    email: Email
    name: str = Field("", max_length=200)
    password: Password
    # The team's name, which the workspace takes as typed; the route refuses it blank.
    workspace: str = Field(max_length=200)
    # As at a sign-up: the browser's zone.
    time_zone: TimeZone = ""


class SignupIn(BaseModel):
    """The address an account is asked for. The rest is chosen on the page its link opens, by
    whoever holds the address."""

    email: Email


class SignupLinkOut(BaseModel):
    """The address a sign-up link was sent to, which the account it makes will have."""

    email: str


class LinkTokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class SignupFinishIn(LinkTokenIn):
    """What the page a sign-up link opens makes the account with."""

    name: str = Field("", max_length=200)
    password: Password
    # The browser's zone, for the dates in the account's mail; one the app does not know is
    # left out rather than refused.
    time_zone: TimeZone = ""


class LoginIn(BaseModel):
    email: Email
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class LoginOut(BaseModel):
    """Whether the password was enough, or an authenticator code must follow."""

    mfa_required: bool


class EmailIn(BaseModel):
    email: Email


class PasswordResetIn(LinkTokenIn):
    password: Password


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: Password


class CodeIn(BaseModel):
    """An authenticator code, or a recovery code where one is accepted."""

    code: str = Field(min_length=1, max_length=40)


class MfaDisableIn(BaseModel):
    """Either the password or a code confirms the change; one must be given."""

    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    code: str | None = Field(None, max_length=40)


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_url: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class AccountDeletionIn(BaseModel):
    """What confirms a request to delete the account: the password, or, for an account that
    has none and signs in through a provider, its email address typed out."""

    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    email: str | None = Field(None, max_length=320)


# The kinds of notice (NOTICE_KINDS in app/models.py, app/notices.py).
NoticeKind = Literal[
    "upload_indexed",
    "project_export",
    "checks_run",
    "review_assigned",
    "report_ready",
    "weekly_digest",
]
# The kinds of notice no longer told, whose notices stay in the centres to read
# (PAST_NOTICE_KINDS in app/models.py).
PastNoticeKind = Literal["mentioned", "thread_replied"]


class NoticeChoiceIn(BaseModel):
    """A change to how the user is told one kind of notice; a way left out stays as it is."""

    kind: NoticeKind
    # In the app's notification centre.
    app: bool | None = None
    # By email (app/postbox.py); turning it on writes to an address the mail provider said to
    # spare (app/bounces.py) again.
    email: bool | None = None


class NoticeChoiceOut(BaseModel):
    """How the user is told one kind of notice: in the app's notification centre, by email,
    both or neither."""

    kind: NoticeKind
    app: bool
    email: bool


class ProfileIn(BaseModel):
    """What the account page changes about its user; a field left out stays as it is."""

    name: str | None = Field(None, max_length=200)
    time_zone: TimeZone | None = None
    notices: list[NoticeChoiceIn] | None = Field(None, max_length=20)


class OnboardingIn(BaseModel):
    """Whether the projects page hides its list of first steps, whether the tour of a
    project's workspace was seen, and whether the chat hides its suggestion to invite the team
    (app/onboarding.py); a field left out stays as it is."""

    checklist_hidden: bool | None = None
    tour_seen: bool | None = None
    invite_hidden: bool | None = None


class OnboardingStepOut(BaseModel):
    """One of the organisation's first steps, and whether it is taken."""

    key: Literal["upload", "ask", "invite"]
    done: bool


class OnboardingOut(BaseModel):
    """The first steps the member's role is offered (app/onboarding.py), in the order the
    projects page lists them, each with whether the organisation has taken it."""

    steps: list[OnboardingStepOut]


class EmailChangeIn(BaseModel):
    """The address to move the account to, confirmed as a deletion is: by the password, or, for
    an account that has none and signs in through a provider, its current address typed out."""

    email: Email
    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    current_email: str | None = Field(None, max_length=320)


class EmailChangeOut(BaseModel):
    """What became of a request to move the account: a link mailed to the new address, which
    makes the change once opened, or, where the install mails nothing, the account of an owner
    or admin moved at once (`moved`), who has nobody above them to ask for the link."""

    moved: bool


class MeteredOut(BaseModel):
    """What the organisation used over one UTC day, or one month from its first day (`metered`
    in app/usage.py): the pages its uploads brought in, the questions it asked, and what all its
    work cost, to its owners and admins alone (app/costs.py)."""

    start: dt.date
    pages: int
    questions: int
    cost_micros: int | None = None

    @field_serializer("cost_micros")
    def _withheld(self, cost: int | None) -> int | None:
        return cost if costs_shown() else None


class UsagePageOut(BaseModel):
    """What the workspace holds now and what it used, day by day and month by month, for the
    usage page (app/api/usage.py)."""

    # The first day of the month in UTC the last of `months` counts, and of the next.
    month: dt.date
    next_month: dt.date
    # What it holds now: its projects, documents and their bytes with its members' My files,
    # and its members with the open invitations (`holdings` in app/usage.py).
    projects: int
    documents: int
    storage_bytes: int
    members: int
    # The characters of text a document without pages counts a page for (app/usage.py).
    chars_per_page: int
    # The documents of the sample project, which `projects` and `documents` leave out
    # (app/sample); 0 once it is deleted.
    sample_documents: int
    # Every day of the last few up to today, and every month of the last few up to this one,
    # the oldest first.
    days: list[MeteredOut]
    months: list[MeteredOut]


class OrganizationOut(_FromORM):
    id: int
    name: str
    slug: str
    # Its settings, as its owners and admins chose them: the language its documents are
    # searched in (a text search configuration, app/languages.py), None for the deployment's;
    # the days it keeps a chat after its last question (app/retention.py) and an audit log
    # entry (app/audit.py), None until they are deleted; and how its members are told each kind
    # of notice until they choose for themselves, in the order of NOTICE_KINDS.
    language: str | None
    chat_days: int | None
    audit_days: int | None
    notice_defaults: list[NoticeChoiceOut] = []
    # Its security settings (app/security.py): whether everyone who works in it signs in with
    # two-factor, and the days a session may work in it after signing in, None for as long as
    # the deployment keeps one, which is the most it may choose (`max_session_days`).
    mfa_required: bool
    session_days: int | None
    max_session_days: int | None = None
    # Whether its members may share a document by a link that needs no account
    # (app/share_links.py).
    share_links: bool = True


class MeOut(BaseModel):
    """The signed-in user, and the organisation and role they act with."""

    id: int
    email: str
    name: str
    # The zone the account's mail is dated in, empty for UTC until its user chooses one.
    time_zone: str
    # How the user is told each kind of notice, the news they may do without (app/notices.py),
    # in the order of NOTICE_KINDS.
    notices: list[NoticeChoiceOut]
    email_verified: bool
    mfa_enabled: bool
    # Whether the organisation requires two-factor sign-in and the account signs in without
    # it: until it is set up, the app offers nothing there but that and signing out
    # (app/security.py).
    mfa_due: bool
    # Providers the account can sign in through, such as "google".
    identities: list[str]
    # The workspace and the user's role in it; None for a member an admin removed, who belongs to
    # no workspace until invited again, which the app then says (app/api/deps.py).
    organization: OrganizationOut | None
    role: str | None
    # Whether the account has a password, which then confirms its deletion.
    has_password: bool
    # When the account will be purged, if its user asked for it to be deleted
    # (app/retention.py); until then the app offers nothing but keeping it.
    deletion_due_at: dt.datetime | None
    # Whether the user hid the projects page's list of first steps, whether they ended the tour
    # of a project's workspace, and whether they hid the chat's suggestion to invite their team,
    # for every organisation (app/onboarding.py).
    checklist_hidden: bool
    tour_seen: bool
    invite_hidden: bool


class OrganizationChangeIn(BaseModel):
    """What the organisation page changes about the current organisation; a field left out
    stays as it is, a null `language` goes back to the deployment's, and a null `chat_days` or
    `audit_days` keeps chats or audit log entries until they are deleted."""

    name: str | None = Field(None, max_length=200)
    # A text search configuration's name, which the database must have.
    language: str | None = Field(None, pattern=r"^[a-z_]{1,63}$")
    chat_days: int | None = Field(None, ge=1, le=36500)
    audit_days: int | None = Field(None, ge=1, le=36500)
    notices: list[NoticeChoiceIn] | None = Field(None, max_length=20)
    # Turned on only by someone who signs in with two-factor themselves.
    mfa_required: bool | None = None
    # Never more than the deployment's SESSION_DAYS.
    session_days: int | None = Field(None, ge=1, le=36500)
    # Off, every share link the organisation has stops.
    share_links: bool | None = None


class LanguagesOut(BaseModel):
    """The languages an organisation may search its documents in, the text search
    configurations the database has, and the deployment's, which it has until it chooses."""

    languages: list[str]
    default: str


class MemberOut(BaseModel):
    """A member of the organisation, as its other members see them."""

    user_id: int
    name: str
    email: str
    role: str
    joined_at: dt.datetime
    # Whether they sign in with two-factor, which the owners and admins are shown, to know whom
    # requiring it would hold; None for anyone else.
    mfa_enabled: bool | None = None
    # When one of their sessions was last seen, to LAST_SEEN_RESOLUTION, for the owners and
    # admins, who answer the team's questions about its accounts; None for anyone else, and for
    # a member with no session kept (app/auth.py prunes them CREDENTIAL_HISTORY after they end).
    last_seen_at: dt.datetime | None = None


# ROLES in app/models.py, which the database's check constraints hold.
Role = Literal["owner", "admin", "member", "viewer"]


class MemberRoleIn(BaseModel):
    """The role a member of the organisation is to have from now on."""

    role: Role


class InvitationIn(BaseModel):
    """An address to invite to the organisation, and the role it joins with."""

    email: Email
    role: Role = "member"


class InvitationOut(BaseModel):
    """An invitation still open, or expired and not sent again, as its organisation's owners
    and admins see it."""

    id: int
    email: str
    role: str
    # Who sent it last, by name or address; None once their account is gone.
    invited_by: str | None
    created_at: dt.datetime
    expires_at: dt.datetime
    expired: bool


class InvitationSentOut(InvitationOut):
    """An invitation just made or sent again, with its link for the owner or admin who sent it
    to copy and hand on: the one way it reaches its address where the install has no mail
    relay (`mailed` false), and never shown again, since only its hash is kept."""

    link: str
    mailed: bool


class HandedLinkOut(BaseModel):
    """A link made by an owner or admin for a member, to hand on where the install mails
    nothing: one that sets their password, or one that moves their account to a new address;
    and when it stops working."""

    link: str
    expires_at: dt.datetime


class EmailLinkIn(BaseModel):
    """The address a member asked an owner or admin to move their account to."""

    email: Email


# API_KEY_SCOPES in app/models.py, which the database's check constraint holds.
ApiKeyScope = Literal["read", "write"]


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: ApiKeyScope = "read"
    # How many days it runs, a year at most; None runs until it is revoked.
    days: int | None = Field(None, ge=1, le=366)


class ApiKeyOut(BaseModel):
    """An API key of the organisation, as its owners and admins see it (app/apikeys.py): never
    the key itself, which is shown once, as it is made or rolled."""

    id: int
    name: str
    scope: ApiKeyScope
    # Its first characters, to tell it from the others.
    hint: str
    # The member it acts as, and their name or address as it is now.
    user_id: int
    made_by: str
    created_at: dt.datetime
    rolled_at: dt.datetime | None
    last_used_at: dt.datetime | None
    expires_at: dt.datetime | None
    expired: bool


class ApiKeyMadeOut(ApiKeyOut):
    """A key just made or rolled, with the key itself, which nothing shows again."""

    key: str


# The days a share link may run (DAYS in app/share_links.py).
ShareDays = Literal[1, 7, 30]
# What a share link shows: a document, or an answer of a chat.
SharedKind = Literal["document", "answer"]


class ShareLinkIn(BaseModel):
    """A link to make to a document: how many days it runs, and its password, if any."""

    days: ShareDays = 7
    password: str | None = Field(
        None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )


class ShareLinkOut(_FromORM):
    """A link to a document or an answer that has not expired, as the members see it
    (app/share_links.py): never its token, which only its maker saw, once."""

    id: int
    kind: SharedKind
    # The document it shows, or the answer.
    document_id: int | None
    message_id: int | None
    # Its maker, by id for the page to tell its member's own, and by name as it is now.
    user_id: int
    made_by: str
    created_at: dt.datetime
    expires_at: dt.datetime
    # Whether it asks for a password.
    password: bool
    # How often it was opened, and when last.
    views: int
    last_viewed_at: dt.datetime | None


class ShareLinkMadeOut(ShareLinkOut):
    """A link just made, with its address, which nothing shows again."""

    url: str


class OrganizationShareLinkOut(ShareLinkOut):
    """A link of the organisation's, as its owners and admins list them, with what it shows: a
    document by its name, an answer by none, since a chat's words are its members'."""

    filename: str | None
    project_id: int
    project_name: str


class SharedOpenIn(BaseModel):
    """A share link's token, from its address, and the password it asks for, if it does."""

    token: str = Field(min_length=1, max_length=100)
    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)


class SharedDocumentOut(BaseModel):
    """What a document's share link shows (app/share_links.py): the document, read only, with
    who shared it, and the link's id, whose pages and file the view's cookie reaches."""

    kind: Literal["document"] = "document"
    link_id: int
    filename: str
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    mime_type: str
    page_count: int | None
    # The member who made the link, by name as it is now, and their organisation's name.
    shared_by: str
    organization: str
    # When the link ends, and when this opening of it does, after which the page opens the
    # link again.
    expires_at: dt.datetime
    view_ends_at: dt.datetime
    content: DocumentContent


class SharedPassageOut(BaseModel):
    """A passage an answer cites, as a share link shows it: its text alone, with where it is."""

    id: int
    document_title: str
    context: str
    text: str
    page_start: int | None
    page_end: int | None


class SharedAnswerOut(BaseModel):
    """What an answer's share link shows: its question, its text, which cites passages as
    [P<id>], and those passages, with who shared it; nothing else of the documents."""

    kind: Literal["answer"] = "answer"
    link_id: int
    question: str
    answer: str
    answered_at: dt.datetime
    passages: list[SharedPassageOut]
    shared_by: str
    organization: str
    expires_at: dt.datetime


SharedOut = Annotated[SharedDocumentOut | SharedAnswerOut, Field(discriminator="kind")]


# WEBHOOK_EVENTS and WEBHOOK_DELIVERY_STATUSES in app/models.py, which the database's check
# constraints hold.
WebhookEvent = Literal[
    "document.ready", "document.failed", "check_run.completed", "report.completed"
]
WebhookDeliveryStatus = Literal["pending", "delivered", "failed"]
# The longest address a webhook takes, as `webhooks.url` holds it.
WEBHOOK_URL_CHARS = 2000
WebhookUrl = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=WEBHOOK_URL_CHARS)
]


class WebhookIn(BaseModel):
    url: WebhookUrl
    events: list[WebhookEvent] = Field(min_length=1, max_length=10)


class WebhookPatch(BaseModel):
    """A webhook changed: a field left out stays as it is."""

    url: WebhookUrl | None = None
    events: list[WebhookEvent] | None = Field(None, min_length=1, max_length=10)
    # False pauses it: its events are not sent until it is on again.
    enabled: bool | None = None


class WebhookDeliveryOut(BaseModel):
    """An event sent to a webhook, as its delivery log shows it (app/webhooks.py): never what it
    said, which the program that took it holds."""

    id: int
    event_id: str
    event_type: WebhookEvent
    # The document's or the run's id.
    subject_id: int
    status: WebhookDeliveryStatus
    attempts: int
    # How the last try ended: the address's status, and the sentence why it was no delivery.
    response_status: int | None
    error: str | None
    created_at: dt.datetime
    attempted_at: dt.datetime | None
    retry_at: dt.datetime | None
    delivered_at: dt.datetime | None


class WebhookOut(BaseModel):
    """A webhook of the organisation, as its owners and admins see it: never its secret, which
    is shown once, as it is made or rolled."""

    id: int
    url: str
    events: list[WebhookEvent]
    enabled: bool
    # Who added it, by name or address as the account is now; None once it is gone.
    made_by: str | None
    created_at: dt.datetime
    rolled_at: dt.datetime | None
    # Its newest delivery, for the list's line.
    last_delivery: WebhookDeliveryOut | None


class WebhookMadeOut(WebhookOut):
    """A webhook just added, or given a new secret, with the secret, which nothing shows again."""

    secret: str


class WebhookDeliveriesOut(BaseModel):
    """A page of a webhook's delivery log, the newest first; `next_before` is the `before` of the
    page that follows, None on the last."""

    deliveries: list[WebhookDeliveryOut]
    next_before: int | None


class InvitationLinkOut(BaseModel):
    """What an invitation's link is for, as its page shows it before anything is done."""

    organization: str
    role: str
    # The address it was sent to, which the account that accepts it must have.
    email: str
    # Who sent it, by name or address, "" once their account is gone.
    invited_by: str
    expires_at: dt.datetime
    expired: bool
    # Whether an account has the address, which then signs in to accept rather than signing up.
    has_account: bool


class InvitationSignupIn(LinkTokenIn):
    """The account a new person makes on an invitation's page, as a sign-up link's page makes
    one, without an organisation of its own."""

    name: str = Field("", max_length=200)
    password: Password
    time_zone: TimeZone = ""


class OwnershipIn(BaseModel):
    """Whom the organisation is handed to, confirmed as an account's deletion is: by the
    password, or, for an account that has none and signs in through a provider, its address
    typed out."""

    user_id: int
    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    email: str | None = Field(None, max_length=320)


class SessionOut(BaseModel):
    id: int
    user_agent: str
    ip: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    # The session the request itself came with.
    current: bool


class OAuthProviderOut(BaseModel):
    name: str
    label: str


class AuditEventOut(BaseModel):
    """One row of the organisation's audit log (app/audit.py)."""

    id: int
    occurred_at: dt.datetime
    # One of AUDIT_ACTIONS in app/models.py, such as "document_deleted".
    action: str
    # The member who acted, or whose account a sign-in was for, as the account is now; all
    # three None for what the operator or the app did, and once the account is deleted.
    user_id: int | None
    user_name: str | None
    user_email: str | None
    ip: str | None
    project_id: int | None
    # The project's name as it is now, None once it is gone.
    project_name: str | None = None
    # What it was done to: "document" and its id. The object may be gone since.
    target_type: str | None
    target_id: int | None
    # Names and settings as they were: {"filename": "invoice.pdf"}.
    detail: dict[str, Any]


class AuditPage(BaseModel):
    """A page of the audit log, the newest row first."""

    events: list[AuditEventOut]
    # What to pass as `before` for the next, older page; None on the last one.
    next_before: int | None


class NotificationOut(BaseModel):
    """A notice in the user's notification centre (app/notices.py)."""

    id: int
    kind: NoticeKind | PastNoticeKind
    title: str
    body: str
    # The page it leads to, a path of the app.
    link: str
    created_at: dt.datetime
    read_at: dt.datetime | None


class NotificationPage(BaseModel):
    """A page of the user's notices, the newest first, and how many of all of them are unread."""

    notifications: list[NotificationOut]
    unread: int
    # What to pass as `before` for the next, older page; None on the last one.
    next_before: int | None


class NotificationsReadIn(BaseModel):
    """The notices to mark read, of the user's own; none named marks every one."""

    ids: list[int] | None = Field(None, max_length=200)


class AuthOptions(BaseModel):
    """What the sign-in pages can offer besides a password, whether anyone may sign up (while
    nobody may, the pages say to ask the team's admin for an invitation), `setup_needed`
    while the install has no account yet, which every page then sends to the setup
    (app/signups.py), and whether the install mails anything (`relay_set` in app/mail.py):
    without, a forgotten password's page says to ask an owner or admin for the link."""

    providers: list[OAuthProviderOut]
    signup_open: bool
    setup_needed: bool
    mail: bool


class LLMStatus(BaseModel):
    """Whether the models can be used. The names are the operator's, given in development only
    (`status` in main.py); `error` is a sentence for whoever reads the status: what to change in
    development, what members cannot do for now in a deployment."""

    available: bool
    agent_model: str | None = None
    index_model: str | None = None
    # What is asked when the model above cannot answer (AGENT_FALLBACK_MODEL,
    # INDEX_FALLBACK_MODEL); None without one.
    agent_fallback_model: str | None = None
    index_fallback_model: str | None = None
    error: str | None


class EmbeddingStatus(BaseModel):
    """Whether the embeddings endpoint answers; its model is named in development only, like
    the models above."""

    ok: bool
    model: str | None = None
    error: str | None


class WorkerStatus(BaseModel):
    """Whether a worker takes the queue's work (app/jobs.py), without which uploads wait to be
    indexed and questions to be answered; `error` is worded as the models' is."""

    running: bool
    error: str | None


class UploadLimitsOut(BaseModel):
    """What one upload request may carry (MAX_UPLOAD_FILES, MAX_UPLOAD_REQUEST_MB, MAX_UPLOAD_MB),
    so that the page sends a folder of more in several and leaves a file of more unsent, and the
    file types the app reads, by their extensions, which the upload client leaves the others
    out by (app/client/)."""

    files: int
    request_bytes: int
    file_bytes: int
    extensions: list[str]


class StatusOut(BaseModel):
    llm: LLMStatus
    embeddings: EmbeddingStatus
    workers: WorkerStatus
    # Whether Word, PowerPoint, Excel, RTF and HTML documents are shown as they look
    # (CONVERTER_URL, app/conversion.py); without, they are shown by their text.
    views: bool = False
    # Whether projects may take mail at an address of their own (INBOUND_DOMAIN, app/inbound.py),
    # and whether a folder may be connected from a service the deployment has an OAuth client for
    # (app/connectors/): the page offers neither without.
    email_in: bool = False
    connectors: bool = False
    # The currency the costs on usage are in (PRICE_CURRENCY), an ISO 4217 code.
    currency: str
    uploads: UploadLimitsOut


class HealthOut(BaseModel):
    status: Literal["ok"]
    # The commit the image was built from, or "development" (app/version.py).
    version: str


class PageFrame(BaseModel):
    """A frame of the page's own scripts that an error was raised in: the function's name as
    the browser gives it, minified, and where it is in the built file (`frames` in
    frontend/src/errors.ts)."""

    function: str | None = Field(
        None, max_length=200, pattern=r"^(?:new |async )?[A-Za-z0-9_$.<>\[\]/-]+$"
    )
    file: str = Field(max_length=200, pattern=r"^/assets/[A-Za-z0-9._-]+\.js$")
    line: int = Field(ge=0)
    column: int = Field(ge=0)


class PageErrorIn(BaseModel):
    """An error the page rendered into, as its error boundary reports it (app/tracking.py): the
    error's kind, the status the API answered when it was the API's refusal, the frames of the
    page's scripts, the innermost first, and the page's path without its query. Never its
    message, which may quote what the page showed, nor anything else of the page."""

    name: str = Field(max_length=100, pattern=r"^[A-Za-z_$][A-Za-z0-9_$.]*$")
    status: int | None = Field(None, ge=100, le=599)
    frames: list[PageFrame] = Field(default_factory=list, max_length=50)
    path: str = Field(max_length=200, pattern=r"^/[A-Za-z0-9/_.~*-]*$")


class PageErrorOut(BaseModel):
    # The id the report was logged under, for the page to show as the reference to quote.
    reference: str | None


class HelpArticleOut(BaseModel):
    """A help page (app/help/), filled in for the deployment: the whole article as Markdown, its
    title the first heading, and the line the contents show of it."""

    slug: str
    title: str
    summary: str
    text: str


class HelpOut(BaseModel):
    """The help menu (app/api/help.py): the address members write to for help, where the server
    names one (SUPPORT_EMAIL; without it the help menu's messages go to the workspace's owners
    and admins), the status page, where it has one, whether the install mails anything
    (`relay_set` in app/mail.py): without, the menu sends nothing and says whom to write to; and
    where the running code's source is, which the AGPL has the app offer (SOURCE_URL, else the
    public repository at the running release: `source_url` in app/version.py)."""

    support_email: str | None
    # The public status page (STATUS_PAGE_URL), outside the deployment.
    status_page: str | None
    mail: bool
    source: str


# What a member writes about from the help menu (`SUPPORT_SUBJECTS` in app/mail.py).
FeedbackKind = Literal["problem", "question", "idea"]
# The longest message from the help menu: a page of a letter or two, whatever they pasted
# into it.
MAX_FEEDBACK_CHARS = 10_000


class FailedRequestIn(BaseModel):
    """The last request of the page's that the API refused or failed, by the id its answer
    carried (X-Request-ID, app/logs.py), under which the log keeps it, and its status."""

    reference: str = Field(max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    status: int = Field(ge=400, le=599)


class FeedbackIn(BaseModel):
    """A member's message from the help menu: what about, their words, and what the page
    attaches of where they were. The page is its path with the ids its query names (an
    open chat's, a folder's), never the query's words (`feedbackPage` in
    frontend/src/help/feedback.ts)."""

    kind: FeedbackKind
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_FEEDBACK_CHARS)
    ]
    page: str = Field(
        max_length=400,
        pattern=r"^/[A-Za-z0-9/_.~*-]*(?:\?[a-z]+=[0-9a-z]*(?:&[a-z]+=[0-9a-z]*)*)?$",
    )
    failed: FailedRequestIn | None = None


class FeedbackOut(BaseModel):
    # The id the message was sent under, which its subject names, for the member to quote.
    reference: str
    # Where it went, and so where an answer comes from: SUPPORT_EMAIL, or None for the
    # workspace's owners and admins.
    to: str | None


# The account export (app/export.py): what the app keeps about a person, as the file the
# account page downloads. The app itself never reads it back.


class ExportedIdentity(BaseModel):
    provider: str
    email: str
    linked_at: dt.datetime


class ExportedAccount(BaseModel):
    email: str
    name: str
    time_zone: str
    created_at: dt.datetime
    email_verified_at: dt.datetime | None
    two_factor_enabled_at: dt.datetime | None
    deletion_due_at: dt.datetime | None
    sign_in_providers: list[ExportedIdentity]
    # How each kind of notice is told (app/notices.py).
    notices: list[NoticeChoiceOut]
    # When the list of first steps was hidden, the tour seen and the suggestion to invite the
    # team hidden (app/onboarding.py).
    checklist_hidden_at: dt.datetime | None
    tour_seen_at: dt.datetime | None
    invite_hidden_at: dt.datetime | None
    # When the mail provider said not to write to the address, and why (app/bounces.py).
    mail_spared: "ExportedSpared | None"


class ExportedSpared(BaseModel):
    reason: str
    recorded_at: dt.datetime


class ExportedMembership(BaseModel):
    organization: str
    slug: str
    role: str
    joined_at: dt.datetime
    # The organisation's private projects the user was chosen to see, by name.
    private_projects: list[str]
    # The Monday whose weekly digest of what arrived they were last told (app/digests.py).
    digested_on: dt.date | None = None


class ExportedSession(BaseModel):
    user_agent: str
    ip: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None


class ExportedBrowser(BaseModel):
    """A browser the account signed in with, told apart by its cookie (app/auth.py)."""

    first_signed_in_at: dt.datetime
    last_signed_in_at: dt.datetime


class ExportedMessage(BaseModel):
    role: str
    content: str
    status: str
    notice: str | None
    error: str | None
    created_at: dt.datetime


class ExportedChat(BaseModel):
    organization: str
    project: str
    title: str
    visibility: str
    created_at: dt.datetime
    last_question_at: dt.datetime
    # When the chat, or its project, went into the trash (app/trash.py), where it is still
    # kept; null for one out of it.
    deleted_at: dt.datetime | None = None
    messages: list[ExportedMessage]


class ExportedActivity(BaseModel):
    """A row of an organisation's audit log that names the user (app/audit.py)."""

    organization: str
    action: str
    ip: str | None
    detail: dict[str, Any]
    occurred_at: dt.datetime


class ExportedUsage(BaseModel):
    organization: str
    kind: str
    unit: str
    quantity: int
    model: str | None
    occurred_at: dt.datetime


class ExportedNotification(BaseModel):
    """A notice in the user's notification centre (app/notices.py)."""

    organization: str
    kind: str
    title: str
    body: str
    link: str
    created_at: dt.datetime
    read_at: dt.datetime | None


class ExportedPin(BaseModel):
    """A chat the user pinned to the top of their chat list: one they started, or one another
    member shared with the project."""

    organization: str
    project: str
    chat: str
    pinned_at: dt.datetime


class ExportedMyFileVersion(BaseModel):
    number: int
    size_bytes: int
    by_model: bool
    note: str
    created_at: dt.datetime


class ExportedMyFile(BaseModel):
    """A file of the user's My files in a project (app/my_files.py), with its versions; the files
    themselves are downloaded from the app."""

    organization: str
    project: str
    name: str
    kind: MyFileKind
    created_at: dt.datetime
    updated_at: dt.datetime
    versions: list[ExportedMyFileVersion]


class ExportedFavorite(BaseModel):
    """A document the user starred, which the library lists among their favourites."""

    organization: str
    project: str
    document: str
    starred_at: dt.datetime


class ExportedVisit(BaseModel):
    """A document the user opened lately, which the library lists among their recent ones: the
    last time they opened it (app/recents.py)."""

    organization: str
    project: str
    document: str
    opened_at: dt.datetime


class ExportedQuestionSet(BaseModel):
    """A question set the user made (app/question_sets.py), with its questions, their words; the
    reports of its answers are the organisation's, about its documents, and stay out."""

    organization: str
    project: str
    title: str
    questions: list[str]
    schedule: str | None
    weekday: int | None
    hour: int
    on_arrivals: bool
    audience: str
    enabled: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class ExportedShareLink(BaseModel):
    """A link the user made to a document or an answer that has not expired
    (app/share_links.py), never its token."""

    organization: str
    project: str
    kind: str = "document"
    # The document's name; None for an answer.
    document: str | None
    created_at: dt.datetime
    expires_at: dt.datetime
    password: bool
    views: int
    last_viewed_at: dt.datetime | None


class AccountExport(BaseModel):
    exported_at: dt.datetime
    # What the file holds and what it leaves out, in words, for whoever reads it.
    about: str
    account: ExportedAccount
    organizations: list[ExportedMembership]
    sessions: list[ExportedSession]
    browsers: list[ExportedBrowser]
    chats: list[ExportedChat]
    pinned_chats: list[ExportedPin] = []
    favorite_documents: list[ExportedFavorite] = []
    recent_documents: list[ExportedVisit] = []
    share_links: list[ExportedShareLink] = []
    question_sets: list[ExportedQuestionSet] = []
    my_files: list[ExportedMyFile] = []
    notifications: list[ExportedNotification]
    usage: list[ExportedUsage]
    activity: list[ExportedActivity]


# A project's export (app/export.py): the export as the projects page lists it, and the JSON its
# ZIP file holds, `project.json` and one `documents/<id>.json` a document.

# queued (being made), ready (to download until `expires_at`), failed (`error` says why).
ExportStatus = Literal["queued", "ready", "failed"]


class ProjectExportIn(BaseModel):
    """What to export: the documents a member chose in the library alone, or, without them, the
    whole project."""

    document_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_CHOSEN)


class ProjectExportOut(_FromORM):
    id: int
    status: ExportStatus
    # How many documents the member chose, for an export of those alone; None for the whole
    # project.
    chosen: int | None = None
    # How many originals and bytes the file holds; 0 until it is ready.
    documents: int
    size_bytes: int
    # Why it could not be made, in the member's words.
    error: str | None
    created_at: dt.datetime
    # Until when it can be downloaded; None until it is ready.
    expires_at: dt.datetime | None


class ExportedProject(BaseModel):
    name: str
    description: str
    visibility: str
    created_at: dt.datetime


class ExportedBy(BaseModel):
    name: str
    email: str


class ExportedVersion(BaseModel):
    """An earlier version of a document, kept when a new file took its place: what it was, and
    where the archive holds its file."""

    number: int
    filename: str
    # The file's path in the archive; None when the malware scan had not passed it.
    original: str | None
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    uploaded_at: dt.datetime
    replaced_at: dt.datetime


class ExportedNote(BaseModel):
    """A document's note, with the name of whoever changed it last as it is now, None for an
    account gone, and when."""

    text: str
    changed_by: str | None
    changed_at: dt.datetime


class ExportedDocument(BaseModel):
    """A document as `project.json` lists it, with where the archive holds its original and
    what was read from it."""

    id: int
    filename: str
    # The folder's path from the project's root, "" for the root.
    folder: str
    # The original's path in the archive; None when the malware scan has not passed it.
    original: str | None
    data: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: str
    title: str | None
    doc_type: str | None
    doc_date: dt.date | None
    # The text search configuration its words are searched in, None for its organisation's.
    language: str | None = None
    # Those of the four above a member set in place of what its reading found (app/details.py).
    edited_details: list[str] = []
    summary: str | None
    # The kind of the extraction template its facts were read for, if any.
    template: str | None
    page_count: int | None
    uploaded_at: dt.datetime
    # Its review, if it is in one: its state, the name of the member it is assigned to, and when
    # it was moved to its state.
    review: str | None = None
    assignee: str | None = None
    review_changed_at: dt.datetime | None = None
    # Which version its file is, and the files it had before, the latest first
    # (app/versions.py).
    version: int = 1
    versions: list[ExportedVersion] = []
    # Its note, which the project's members share (app/notes.py), if it has one.
    note: ExportedNote | None = None


class ExportedBlock(BaseModel):
    """A block of a document's text as its parse read it: a heading, a paragraph, a list item or
    a table."""

    kind: str
    level: int | None
    text: str
    page: int | None


class ExportedVersionText(BaseModel):
    """An earlier version's text, block by block."""

    number: int
    blocks: list[ExportedBlock]


class ExportedSection(BaseModel):
    """A node of a document's outline; `parent_id` names another of its sections."""

    id: int
    parent_id: int | None
    level: int
    title: str
    path: str
    summary: str | None
    page_start: int | None
    page_end: int | None


class ExportedPassage(BaseModel):
    """A passage, which answers cite as [P<id>], in the section `section_id` names."""

    id: int
    section_id: int
    text: str
    context: str
    page_start: int | None
    page_end: int | None


class ExportedFact(BaseModel):
    name: str
    value_type: str
    value: str
    number: float | None
    date: dt.date | None
    currency: str | None
    quote: str | None
    page: int | None
    passage_id: int | None
    # Whether the quote was found in the document as the model gave it.
    verified: bool
    # A member's check of it, with their value in `value` when they corrected it.
    check: FactCheckOut | None


class ExportedDocumentData(BaseModel):
    """What was read from one document: `documents/<id>.json` in the archive."""

    id: int
    filename: str
    title: str | None
    sections: list[ExportedSection]
    passages: list[ExportedPassage]
    facts: list[ExportedFact]
    # The text of each earlier version, the latest first, which nothing reads for answers.
    versions: list[ExportedVersionText] = []


class ExportedProjectChat(BaseModel):
    title: str
    visibility: str
    # The name of the member who started it, as it is now.
    started_by: str
    created_at: dt.datetime
    last_question_at: dt.datetime
    messages: list[ExportedMessage]


class ExportedTemplate(BaseModel):
    """An extraction template of the project's own, as its members wrote it."""

    doc_type: str
    title: str
    description: str
    fields: list[TemplateFieldOut]


class ExportedProjectShareLink(BaseModel):
    """A link to one of the project's documents, or to an answer in one of its chats, that has
    not expired, never its token."""

    kind: str = "document"
    # The document's id and name; None for an answer.
    document_id: int | None
    document: str | None
    # The name of the member who made it, as it is now.
    made_by: str
    created_at: dt.datetime
    expires_at: dt.datetime
    password: bool
    views: int


class ProjectExportFile(BaseModel):
    """`project.json` in the archive."""

    exported_at: dt.datetime
    # What the archive holds and what it leaves out, in words, for whoever reads it.
    about: str
    organization: str
    project: ExportedProject
    exported_by: ExportedBy
    # Every folder's path from the project's root, an empty one too.
    folders: list[str]
    documents: list[ExportedDocument]
    chats: list[ExportedProjectChat]
    # The project's own extraction templates; the built-in ones are Emudoc's, and not in it.
    templates: list[ExportedTemplate]
    # The links that show its documents to whoever holds them (app/share_links.py).
    share_links: list[ExportedProjectShareLink] = []


# The Administration page (app/api/admin.py): what the maintenance commands print, as the page
# shows the workspace's owners and admins. Amounts are millionths of PRICE_CURRENCY, as the
# usage rows hold them.


class CostOut(BaseModel):
    """What the workspace's work cost over a span of days, against DAILY_SPEND_CAP (None for
    none): its costliest day, and the days that reached the cap."""

    cost_micros: int | None
    calls: int
    input_tokens: int
    output_tokens: int
    answers: int
    pages: int
    top_day_micros: int | None
    limit_micros: int | None
    days_at_limit: int | None


class ModelCostOut(BaseModel):
    kind: str
    model: str
    cost_micros: int | None
    calls: int
    input_tokens: int
    output_tokens: int
    # The tokens recorded without a price, which no cost counts.
    unpriced_tokens: int


class AdminCostsOut(BaseModel):
    since: dt.date
    until: dt.date
    currency: str
    # The workspace's, against DAILY_SPEND_CAP.
    cost: CostOut
    models: list[ModelCostOut]


class QueueOut(BaseModel):
    """One kind of job by state (JOB_STATES in app/jobs.py), and how long the oldest due one
    has waited for a worker."""

    kind: str
    due: int
    waiting: int
    running: int
    failed: int
    oldest_due_seconds: float | None


class AdminJobOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    max_attempts: int
    run_at: dt.datetime
    lease_until: dt.datetime | None
    finished_at: dt.datetime | None
    worker_id: str | None
    # What it is for, as "acme / Contracts / invoice.pdf" (`NamedJob.about`).
    about: str
    # Why it failed, in the words of the one who runs the server; None for one that did not.
    last_error: str | None
    # Whether the alerter mailed its failure to ALERT_EMAIL.
    reported: bool


class AdminWorkerOut(BaseModel):
    """A worker seen within WORKERS_KEPT (app/jobs.py): running, stopped by itself, or neither,
    which is a process that died or lost the database."""

    id: str
    version: str
    started_at: dt.datetime
    last_seen_at: dt.datetime
    stopped_at: dt.datetime | None
    running: bool


class AdminJobsOut(BaseModel):
    taken_at: dt.datetime
    queue: list[QueueOut]
    running: list[AdminJobOut]
    # The dead letters of the last `history_days`, the newest first, `failed_shown` at most.
    failed: list[AdminJobOut]
    failed_total: int
    history_days: int
    # The workers seen lately, the latest first; none running leaves the queue's work waiting.
    workers: list[AdminWorkerOut]


class RequeueIn(BaseModel):
    """The failed jobs to run again, by id; None for every one the workspace's queue keeps, as
    the `failed-jobs --requeue` command runs them."""

    job_ids: list[int] | None = Field(None, max_length=1000)


class RequeueOut(BaseModel):
    # How many documents were queued again, and what was done with each job, one line each.
    queued: int
    lines: list[str]


class AdminModelOut(BaseModel):
    variable: str
    name: str
    role: str
    fallback: bool
    # Whether its provider is switched off, by DISABLED_PROVIDERS or from the page.
    disabled: bool


class AdminProviderOut(BaseModel):
    """A model provider the settings name a model of, or that the page holds switched off, and
    whether it is: by DISABLED_PROVIDERS, which the page cannot switch back on, or from the page,
    by whom and since when."""

    name: str
    # The settings that name a model of it (AGENT_MODEL, …), none for one no longer named.
    variables: list[str]
    off: bool
    by_setting: bool
    switched_at: dt.datetime | None
    # Who switched it off, by name or address; None for none, or one whose account is gone.
    switched_by: str | None


class ProviderSwitchIn(BaseModel):
    off: bool


class ConcernOut(BaseModel):
    """What the worker's watch sees wrong now (app/watch.py), with since when it was raised and
    mailed, None for one the watch has not looked at yet."""

    key: str
    title: str
    message: str
    raised_at: dt.datetime | None


class DatabaseOut(BaseModel):
    version: str
    role: str
    vector: str | None
    iterative_scans: bool
    # The role's statement_timeout and idle_in_transaction_session_timeout, "0" for none.
    timeouts: dict[str, str]


class AdminServicesOut(BaseModel):
    models: list[AdminModelOut]
    providers: list[AdminProviderOut]
    # What stops the models, in the words of the one who runs the server, None while they can
    # be used.
    model_problem: str | None
    embedding_model: str
    embedding_address: str
    scanner: bool
    # How the mail goes: "smtp", "resend", or None where nothing is mailed (`sent_through` in
    # app/mail.py).
    mail: Literal["smtp", "resend"] | None
    # Where the files are kept: the bucket's origin, or None for a directory.
    storage_origin: str | None
    concerns: list[ConcernOut]
    # The checks that could not look, whose concerns are unknown (the watch's key prefixes).
    unchecked: list[str]
    database: DatabaseOut
