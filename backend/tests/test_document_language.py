"""A document's own language (app/languages.py): detected from its words as it is parsed, its
passages searched in it, left alone by a change of its organisation's language, and set by a
member as its other details are (app/details.py), its passages then moved by the organisation's
language job."""

from sqlalchemy import select, text

from app.agent.tools import ListDocumentsInput, ToolContext, list_documents
from app.api.documents import BOTH_WAYS
from app.indexing.pipeline import process_document
from app.jobs import APPLY_LANGUAGE
from app.languages import DETECT_CHARS, UNKNOWN_LANGUAGE, WORDS_AS_WRITTEN, detect_language
from app.models import Job, Passage
from tests.test_api import create_project
from tests.test_details import details
from tests.test_organization_settings import change, run_language, text_upload
from tests.test_versions import given

GERMAN = (
    "Die Lieferungen erfolgen jede Woche und werden vom Empfänger quittiert. Verspätete "
    "Lieferungen werden dem Lieferanten schriftlich gemeldet."
).encode()
ENGLISH = (
    b"Deliveries arrive every week and are signed for by the receiver. Late deliveries are "
    b"reported to the supplier in writing."
)
POLISH = (
    "Dostawy przychodzą co tydzień i są kwitowane przez odbiorcę. Opóźnione dostawy zgłaszane "
    "są dostawcy na piśmie."
).encode()
# Dates, amounts and a currency: nothing to tell a language by.
NUMBERS = b"2026-03-01 1,250.00 EUR 42\n2026-03-08 980.00 EUR 17\n"
KNOWN = {"english", "german", "french", WORDS_AS_WRITTEN}


def languages_of(session, document_id: int) -> set[str]:
    return set(
        session.scalars(
            select(text("CAST(language AS text)"))
            .select_from(Passage)
            .where(Passage.document_id == document_id)
        )
    )


def document(client, document_id: int) -> dict:
    return client.get(f"/api/documents/{document_id}").json()


def found(client, project_id: int, words: str) -> set[int]:
    """The documents a search finds, by their words alone while the embedder fails."""
    response = client.get(f"/api/projects/{project_id}/search", params={"q": words})
    assert response.status_code == 200, response.text
    return {hit["document_id"] for hit in response.json()["hits"]}


def test_each_documents_language_is_detected_as_it_is_parsed_and_its_words_searched_in_it(
    client, session, services
):
    project = create_project(client)
    german = text_upload(client, services, project["id"], "lieferung.txt", GERMAN)
    english = text_upload(client, services, project["id"], "delivery.txt", ENGLISH)
    polish = text_upload(client, services, project["id"], "dostawy.txt", POLISH)
    numbers = text_upload(client, services, project["id"], "log.txt", NUMBERS)

    read = {
        name: (document(client, id)["read_language"], document(client, id)["language"])
        for name, id in (("de", german), ("en", english), ("pl", polish), ("nums", numbers))
    }
    # Polish is a language the database cannot stem: its words are searched as written. Numbers
    # tell none, and follow the organisation's language, the deployment's English here.
    assert read == {
        "de": ("german", "german"),
        "en": ("english", "english"),
        "pl": (WORDS_AS_WRITTEN, WORDS_AS_WRITTEN),
        "nums": (None, None),
    }
    assert languages_of(session, german) == {"german"}
    assert languages_of(session, polish) == {WORDS_AS_WRITTEN}
    assert languages_of(session, numbers) == {"english"}
    # Each is stemmed in its own: "Lieferung" finds "Lieferungen", "delivery" "deliveries".
    services.embedder.fail = True
    assert found(client, project["id"], "Lieferung") == {german}
    assert found(client, project["id"], "delivery") == {english}
    assert found(client, project["id"], "dostawa") == set()
    assert found(client, project["id"], "dostawy") == {polish}


def test_the_detection_reads_the_first_words_alone_and_names_only_what_the_database_has():
    assert detect_language([GERMAN.decode()], KNOWN) == ("german", "de")
    # A language the database has no configuration for is searched by its words as written, and
    # its code says which it was.
    assert detect_language([GERMAN.decode()], {"english", WORDS_AS_WRITTEN}) == (
        WORDS_AS_WRITTEN,
        "de",
    )
    assert detect_language([POLISH.decode()], KNOWN) == (WORDS_AS_WRITTEN, "pl")
    assert detect_language([NUMBERS.decode(), ""], KNOWN) is None
    assert detect_language([], KNOWN) is None
    # A document in English whose German starts past what is read is English.
    english = ENGLISH.decode() * (DETECT_CHARS // len(ENGLISH) + 1)
    assert detect_language([english, GERMAN.decode() * 500], KNOWN) == ("english", "en")


def test_the_organisations_language_moves_only_the_documents_without_one_of_their_own(
    client, session, services
):
    project = create_project(client)
    german = text_upload(client, services, project["id"], "lieferung.txt", GERMAN)
    numbers = text_upload(client, services, project["id"], "log.txt", NUMBERS)

    change(client, language="french")
    run_language(services)
    assert languages_of(session, german) == {"german"}
    assert languages_of(session, numbers) == {"french"}
    # A document read since takes the organisation's language where it tells none.
    later = text_upload(client, services, project["id"], "more.txt", NUMBERS + b"\n")
    assert languages_of(session, later) == {"french"}
    assert document(client, german)["language"] == "german"


def test_a_member_sets_a_documents_language_which_moves_its_passages_and_holds_through_readings(
    client, session, services
):
    project = create_project(client)
    german = text_upload(client, services, project["id"], "lieferung.txt", GERMAN)

    changed = details(client, german, language="dutch")
    assert (changed["language"], changed["read_language"]) == ("dutch", "german")
    assert changed["edited_details"] == ["language"]
    # The passages move by the organisation's job, which a long document's request would not
    # wait for.
    (job,) = session.scalars(select(Job).where(Job.kind == APPLY_LANGUAGE, Job.status == "queued"))
    assert languages_of(session, german) == {"german"}
    run_language(services)
    assert languages_of(session, german) == {"dutch"}

    # "Index again" reads the language again and keeps the member's.
    assert client.post(f"/api/documents/{german}/reprocess").status_code == 202
    process_document(german, services)
    assert document(client, german)["language"] == "dutch"
    assert languages_of(session, german) == {"dutch"}

    # None is the organisation's language, the member's choice too.
    none = details(client, german, language=None)
    assert (none["language"], none["edited_details"]) == (None, ["language"])
    run_language(services)
    assert languages_of(session, german) == {"english"}

    # Taking the reading's again takes German, and moves the passages back.
    back = details(client, german, as_read=["language"])
    assert (back["language"], back["edited_details"]) == ("german", [])
    run_language(services)
    assert languages_of(session, german) == {"german"}

    # Setting the language it has already queues nothing.
    details(client, german, language="german")
    waiting = select(Job).where(Job.kind == APPLY_LANGUAGE, Job.status == "queued")
    assert session.scalar(waiting) is None

    refused = client.patch(f"/api/documents/{german}/details", json={"language": "klingon"})
    assert (refused.status_code, refused.json()["detail"]) == (400, UNKNOWN_LANGUAGE)
    # Not a configuration's name at all.
    named = client.patch(f"/api/documents/{german}/details", json={"language": "German"})
    assert named.status_code == 422
    both = client.patch(
        f"/api/documents/{german}/details", json={"language": "german", "as_read": ["language"]}
    )
    assert (both.status_code, both.json()["detail"]) == (422, BOTH_WAYS)


def test_a_new_version_is_read_for_its_own_language(client, session, services):
    project = create_project(client)
    first = text_upload(client, services, project["id"], "notice.txt", GERMAN)
    versioned = given(client, services, first, "notice.txt", ENGLISH)
    assert (versioned["read_language"], versioned["language"]) == ("english", "english")
    assert languages_of(session, first) == {"english"}


def test_the_agent_is_told_a_documents_language_where_it_is_not_the_organisations(
    client, session, services
):
    project = create_project(client)
    text_upload(client, services, project["id"], "lieferung.txt", GERMAN)
    text_upload(client, services, project["id"], "delivery.txt", ENGLISH)
    text_upload(client, services, project["id"], "dostawy.txt", POLISH)

    listed = list_documents(
        ToolContext(session, services.embedder, project["id"]), ListDocumentsInput()
    )

    def line(name: str) -> str:
        return next(line for line in listed.splitlines() if f"file {name}" in line)

    assert "written in German" in line("lieferung.txt")
    assert "written in" not in line("delivery.txt")
    assert "searched by its words as written" in line("dostawy.txt")
