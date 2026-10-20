"""The trash: what a member deletes waits there to be restored, until it is deleted for good."""

from pydantic_ai.messages import TextPart
from sqlalchemy import func, select, text

from app.agent.tools import (
    ListDocumentsInput,
    QueryFactsInput,
    ReadInput,
    SearchInput,
    ToolContext,
    list_documents,
    query_facts,
    read,
    search,
)
from app.answers import answer
from app.indexing.pipeline import process_document
from app.jobs import INDEX_DOCUMENT, claim
from app.maintenance import housekeeping
from app.models import Chat, Document, Folder, Project
from app.trash import DOCUMENT_IN_TRASH, NOT_IN_TRASH, PASSAGE_IN_TRASH, TRASH_DAYS
from tests.conftest import FakeTurn
from tests.test_api import create_project, listed, upload
from tests.test_audit import actions
from tests.test_chats import answered, ask, new_chat
from tests.test_entitlements import member, plan  # noqa: F401
from tests.test_folders import make_folder, upload_into


def trash(client, project_id) -> dict:
    response = client.get(f"/api/projects/{project_id}/trash")
    assert response.status_code == 200, response.text
    return response.json()


def held(client, project_id) -> list[tuple[str, str]]:
    """What the project's trash lists, as (kind, name), the latest deleted first."""
    return [(item["kind"], item["name"]) for item in trash(client, project_id)["items"]]


def restore(client, kind, item_id):
    return client.post(f"/api/trash/{kind}/{item_id}/restore")


def hits(client, project_id, query) -> set[int]:
    response = client.get(f"/api/projects/{project_id}/search", params={"q": query})
    assert response.status_code == 200, response.text
    return {hit["document_id"] for hit in response.json()["hits"]}


def backdate(session, table, row_id, days):
    """Move a row's time in the trash back by `days`, as if it had been there that long."""
    session.execute(
        text(
            f"UPDATE {table} SET deleted_at = deleted_at - make_interval(days => :days) "
            "WHERE id = :id"
        ),
        {"days": days, "id": row_id},
    )
    session.commit()


def indexed_upload(client, services, project_id, *paths, folder_id=None) -> list[dict]:
    if folder_id is None:
        documents = upload(client, project_id, *paths)["documents"]
    else:
        documents = upload_into(client, project_id, folder_id, *paths)["documents"]
    for document in documents:
        process_document(document["id"], services)
    return documents


def test_a_deleted_document_waits_in_the_trash_unread_until_it_is_restored(
    client, services, session, account, sample_pdf, sample_docx
):
    project = create_project(client)
    contract, policy = indexed_upload(client, services, project["id"], sample_pdf, sample_docx)
    passage = client.get(f"/api/projects/{project['id']}/search", params={"q": "notice"}).json()[
        "hits"
    ][0]
    assert passage["document_id"] == contract["id"]

    assert client.delete(f"/api/documents/{contract['id']}").status_code == 204
    # Nothing reads it: the library, the counts, the search, the facts, the pages.
    assert [d["filename"] for d in listed(client, project["id"])] == ["policy.docx"]
    assert client.get(f"/api/projects/{project['id']}").json()["document_count"] == 1
    assert contract["id"] not in hits(client, project["id"], "notice")
    facts = client.get(f"/api/projects/{project['id']}/facts").json()
    assert {fact["document_id"] for fact in facts["facts"]} <= {policy["id"]}
    gone = client.get(f"/api/documents/{contract['id']}")
    assert (gone.status_code, gone.json()["detail"]) == (404, DOCUMENT_IN_TRASH)
    cited = client.get(f"/api/passages/{passage['passage_id']}")
    assert (cited.status_code, cited.json()["detail"]) == (404, PASSAGE_IN_TRASH)
    assert client.get(f"/api/documents/{contract['id']}/file").status_code == 404
    # Its files are kept for the restore.
    stored = session.get(Document, contract["id"])
    assert services.storage.exists(stored.storage_key)

    (item,) = trash(client, project["id"])["items"]
    assert item["kind"] == "document" and item["name"] == "msa.pdf"
    assert item["place"] is None and item["deleted_by"] == account.user.name
    assert item["mime_type"] == "application/pdf"
    assert trash(client, project["id"])["days"] == TRASH_DAYS

    restored = restore(client, "documents", contract["id"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "ready"
    assert held(client, project["id"]) == []
    assert {d["filename"] for d in listed(client, project["id"])} == {"msa.pdf", "policy.docx"}
    assert contract["id"] in hits(client, project["id"], "notice")
    assert client.get(f"/api/passages/{passage['passage_id']}").status_code == 200
    assert [action for action, _ in actions(session) if "document_" in action][-2:] == [
        "document_deleted",
        "document_restored",
    ]
    # Restored, it is no longer in the trash to restore or delete again.
    again = restore(client, "documents", contract["id"])
    assert (again.status_code, again.json()["detail"]) == (404, NOT_IN_TRASH)
    assert client.delete(f"/api/trash/documents/{contract['id']}").status_code == 404


def test_the_agent_finds_nothing_in_the_trash(client, services, session, sample_pdf, sample_docx):
    project = create_project(client)
    contract, policy = indexed_upload(client, services, project["id"], sample_pdf, sample_docx)
    passage = session.scalar(
        text("SELECT id FROM passages WHERE document_id = :id ORDER BY ordinal LIMIT 1"),
        {"id": contract["id"]},
    )
    client.delete(f"/api/documents/{contract['id']}")
    ctx = ToolContext(session, services.embedder, project["id"])
    gone, kept = f"doc {contract['id']} ", f"doc {policy['id']} "
    listing = list_documents(ctx, ListDocumentsInput())
    assert "Documents 1-1 of 1." in listing and kept in listing and gone not in listing
    assert gone not in search(ctx, SearchInput(query="notice period"))
    assert "notice_period" not in query_facts(ctx, QueryFactsInput())
    assert "No facts match" in query_facts(ctx, QueryFactsInput(names=["notice_period"]))
    assert read(ctx, ReadInput(passage_id=passage)).startswith("No passage with id")
    assert read(ctx, ReadInput(document_id=contract["id"])) == "Nothing to read there."


def test_a_folder_takes_what_is_in_it_and_brings_back_only_that(
    client, services, session, sample_pdf, sample_docx, sample_xlsx
):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    signed = make_folder(client, project["id"], "Signed", contracts["id"])
    msa, policy = indexed_upload(
        client, services, project["id"], sample_pdf, sample_docx, folder_id=contracts["id"]
    )
    (sheet,) = indexed_upload(client, services, project["id"], sample_xlsx, folder_id=signed["id"])

    # The policy goes first by itself, then the folder with what is left in it.
    assert client.delete(f"/api/documents/{policy['id']}").status_code == 204
    assert client.delete(f"/api/folders/{contracts['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}/folders").json() == []
    assert client.get(f"/api/projects/{project['id']}").json()["document_count"] == 0
    listing = trash(client, project["id"])
    shown = [(i["kind"], i["name"], i["documents"], i["place"]) for i in listing["items"]]
    assert shown == [("folder", "Contracts", 2, None), ("document", "policy.docx", 0, "Contracts")]
    assert listing["total"] == 2
    folder_deleted = [detail for action, detail in actions(session) if action == "folder_deleted"]
    assert folder_deleted == [{"name": "Contracts", "documents": 2}]
    # Nothing can be put in a folder that is in the trash.
    into = client.post(
        f"/api/projects/{project['id']}/folders", json={"name": "x", "parent_id": signed["id"]}
    )
    assert into.status_code == 404
    moved = client.patch(f"/api/documents/{msa['id']}", json={"folder_id": None})
    assert moved.status_code == 404

    restored = restore(client, "folders", contracts["id"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == "Contracts" and restored.json()["document_count"] == 1
    folders = client.get(f"/api/projects/{project['id']}/folders").json()
    assert {(f["name"], f["document_count"]) for f in folders} == {("Contracts", 1), ("Signed", 1)}
    assert [d["id"] for d in listed(client, project["id"], signed["id"])] == [sheet["id"]]
    # The policy was deleted before the folder, and stays in the trash on its own.
    assert held(client, project["id"]) == [("document", "policy.docx")]
    assert restore(client, "documents", policy["id"]).status_code == 200
    names = {d["filename"] for d in listed(client, project["id"], contracts["id"])}
    assert names == {"msa.pdf", "policy.docx"}


def test_a_restore_finds_a_place_and_a_name_of_its_own(client):
    project = create_project(client)
    invoices = make_folder(client, project["id"], "Invoices")
    archive = make_folder(client, project["id"], "2024", invoices["id"])
    note = text_file(client, project["id"], archive["id"], b"The first note.")
    client.delete(f"/api/documents/{note['id']}")
    client.delete(f"/api/folders/{archive['id']}")
    # A new folder and a file take the names the deleted ones had.
    make_folder(client, project["id"], "2024", invoices["id"])
    text_file(client, project["id"], invoices["id"], b"Another note.")

    # The document's folder is in the trash: it comes back into the nearest live one above,
    # under a name of its own there.
    document = restore(client, "documents", note["id"]).json()
    assert (document["folder_id"], document["filename"]) == (invoices["id"], "notes (2).txt")
    folder = restore(client, "folders", archive["id"]).json()
    assert (folder["parent_id"], folder["name"]) == (invoices["id"], "2024 (2)")


def text_file(client, project_id, folder_id, content: bytes) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/documents",
        params={"folder_id": folder_id},
        files=[("files", ("notes.txt", content))],
    )
    assert response.status_code == 201, response.text
    (document,) = response.json()["documents"]
    return document


def test_deleting_for_good_removes_the_rows_and_the_files(
    client, services, session, sample_pdf, sample_docx, sample_xlsx
):
    project = create_project(client)
    outer = make_folder(client, project["id"], "Outer")
    inner = make_folder(client, project["id"], "Inner", outer["id"])
    (msa,) = indexed_upload(client, services, project["id"], sample_pdf, folder_id=inner["id"])
    (policy,) = indexed_upload(client, services, project["id"], sample_docx)
    (sheet,) = indexed_upload(client, services, project["id"], sample_xlsx, folder_id=outer["id"])
    prefixes = [session.get(Document, d["id"]).storage_prefix for d in (msa, policy, sheet)]
    assert all(list(services.storage.list(prefix)) for prefix in prefixes)

    # The inner folder by itself, then the outer one, which holds it; the trash lists both.
    client.delete(f"/api/folders/{inner['id']}")
    client.delete(f"/api/folders/{outer['id']}")
    client.delete(f"/api/documents/{policy['id']}")
    assert held(client, project["id"]) == [
        ("document", "policy.docx"),
        ("folder", "Outer"),
        ("folder", "Inner"),
    ]
    assert client.delete(f"/api/trash/documents/{policy['id']}").status_code == 204
    assert held(client, project["id"]) == [("folder", "Outer"), ("folder", "Inner")]
    # Emptying deletes the outer folder with the inner one in it, and every file below them.
    assert client.delete(f"/api/projects/{project['id']}/trash").status_code == 204
    assert held(client, project["id"]) == []
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Document)) == 0
    assert session.scalar(select(func.count()).select_from(Folder)) == 0
    assert all(list(services.storage.list(prefix)) == [] for prefix in prefixes)
    purged = [(action, detail) for action, detail in actions(session) if "_purged" in action]
    assert purged == [
        ("document_purged", {"filename": "policy.docx"}),
        ("folder_purged", {"name": "Outer"}),
        ("folder_purged", {"name": "Inner"}),
    ]


def test_a_project_in_the_trash_is_nobody_s_until_it_is_restored(
    client, services, session, account, sample_pdf
):
    project = create_project(client)
    (msa,) = indexed_upload(client, services, project["id"], sample_pdf)
    chat = new_chat(client, project["id"])
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get("/api/projects").json() == []
    for path in (
        f"/api/projects/{project['id']}",
        f"/api/projects/{project['id']}/documents",
        f"/api/documents/{msa['id']}",
        f"/api/chats/{chat['id']}",
    ):
        assert client.get(path).status_code == 404, path
    (shown,) = client.get("/api/trash").json()["projects"]
    assert shown["id"] == project["id"] and shown["document_count"] == 1
    assert shown["deleted_by"] == account.user.name
    assert shown["purged_at"] > shown["deleted_at"]
    assert services.storage.exists(session.get(Document, msa["id"]).storage_key)

    restored = restore(client, "projects", project["id"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["document_count"] == 1
    assert client.get("/api/trash").json()["projects"] == []
    assert client.get(f"/api/chats/{chat['id']}").status_code == 200
    assert hits(client, project["id"], "notice") == {msa["id"]}

    client.delete(f"/api/projects/{project['id']}")
    prefix = session.get(Document, msa["id"]).storage_prefix
    assert client.delete(f"/api/trash/projects/{project['id']}").status_code == 204
    session.expire_all()
    assert session.get(Project, project["id"]) is None
    assert list(services.storage.list(prefix)) == []


def test_the_trash_frees_what_the_plan_counts_and_a_restore_is_held_to_it(
    client, services, request, sample_pdf, sample_docx
):
    # The owner of a fresh organisation on the free plan (`member`), held to one of each.
    request.getfixturevalue("member")
    request.getfixturevalue("plan")(projects=1, documents=1)
    project = create_project(client)
    (msa,) = indexed_upload(client, services, project["id"], sample_pdf)
    client.delete(f"/api/documents/{msa['id']}")
    # The trash counts for nothing: the place is the next upload's.
    assert upload(client, project["id"], sample_docx)["rejected"] == []
    refused = restore(client, "documents", msa["id"])
    assert refused.status_code == 402
    assert refused.json()["limit"] == "documents"
    assert refused.json()["detail"].startswith("Restoring this brings back 1 document, and the")

    client.delete(f"/api/projects/{project['id']}")
    other = create_project(client, "Other")
    refused = restore(client, "projects", project["id"])
    assert (refused.status_code, refused.json()["limit"]) == (402, "projects")
    client.delete(f"/api/projects/{other['id']}")
    assert restore(client, "projects", project["id"]).status_code == 200


def test_an_upload_of_bytes_in_the_trash_is_told_to_restore_them(
    client, services, sample_pdf, sample_docx
):
    project = create_project(client)
    folder = make_folder(client, project["id"], "Contracts")
    (msa,) = upload(client, project["id"], sample_pdf)["documents"]
    (policy,) = upload_into(client, project["id"], folder["id"], sample_docx)["documents"]
    client.delete(f"/api/documents/{msa['id']}")
    client.delete(f"/api/folders/{folder['id']}")
    rejected = upload(client, project["id"], sample_pdf, sample_docx)["rejected"]
    assert [file["reason"] for file in rejected] == [
        "Already in this project's trash as 'msa.pdf'. Restore it from there, or delete it "
        "there for good to upload it again.",
        "Already in this project's trash as 'policy.docx', deleted with the folder 'Contracts'. "
        "Restore it from there, or delete it there for good to upload it again.",
    ]
    client.delete(f"/api/trash/documents/{msa['id']}")
    assert upload(client, project["id"], sample_pdf)["rejected"] == []
    assert policy


def test_indexing_waits_while_a_document_is_in_the_trash(client, services, session, sample_pdf):
    project = create_project(client)
    (msa,) = upload(client, project["id"], sample_pdf)["documents"]
    client.delete(f"/api/documents/{msa['id']}")
    # The job that was queued finds it in the trash and runs nothing.
    assert process_document(msa["id"], services) == []
    session.expire_all()
    document = session.get(Document, msa["id"])
    assert document.status == "queued" and document.stages["parse"]["status"] == "pending"
    session.execute(text("DELETE FROM jobs"))
    session.commit()

    # A restore queues what was left.
    assert restore(client, "documents", msa["id"]).json()["status"] == "queued"
    job = claim("test:1", [INDEX_DOCUMENT])
    assert job is not None and job.payload["document_id"] == msa["id"]
    process_document(msa["id"], services)
    assert client.get(f"/api/documents/{msa['id']}").json()["status"] == "ready"


def test_the_housekeeper_purges_what_was_in_the_trash_long_enough(
    client, services, session, sample_pdf, sample_docx, sample_xlsx
):
    project = create_project(client)
    gone = create_project(client, "Old")
    (msa,) = indexed_upload(client, services, project["id"], sample_pdf)
    folder = make_folder(client, project["id"], "Policies")
    (policy,) = indexed_upload(client, services, project["id"], sample_docx, folder_id=folder["id"])
    (sheet,) = indexed_upload(client, services, project["id"], sample_xlsx)
    (kept,) = indexed_upload(client, services, gone["id"], sample_pdf)
    chat = new_chat(client, project["id"])
    for path in (
        f"/api/documents/{msa['id']}",
        f"/api/folders/{folder['id']}",
        f"/api/documents/{sheet['id']}",
        f"/api/projects/{gone['id']}",
        f"/api/chats/{chat['id']}",
    ):
        assert client.delete(path).status_code == 204
    prefixes = {d: session.get(Document, d).storage_prefix for d in (msa["id"], policy["id"])}
    old_prefix = session.get(Document, kept["id"]).storage_prefix
    backdate(session, "documents", msa["id"], TRASH_DAYS)
    for document_id in (policy["id"],):
        backdate(session, "documents", document_id, TRASH_DAYS)
    backdate(session, "folders", folder["id"], TRASH_DAYS)
    backdate(session, "projects", gone["id"], TRASH_DAYS)
    backdate(session, "chats", chat["id"], TRASH_DAYS)

    housekeeping(services.storage)
    session.expire_all()
    remaining = set(session.scalars(select(Document.id)))
    assert remaining == {sheet["id"]}
    assert session.get(Folder, folder["id"]) is None
    assert session.get(Project, gone["id"]) is None
    assert session.get(Chat, chat["id"]) is None
    for prefix in (*prefixes.values(), old_prefix):
        assert list(services.storage.list(prefix)) == []
    # What has been there for less time stays, to be restored.
    assert held(client, project["id"]) == [("document", "budget.xlsx")]


def test_a_chat_in_the_trash_is_its_starter_s_alone(
    client, services, session, account, make_account, act_as, sample_pdf
):
    project = create_project(client)
    indexed_upload(client, services, project["id"], sample_pdf)
    chat = new_chat(client, project["id"])
    services.llm.agent_turns.append(FakeTurn(TextPart("Ninety days.")))
    answered(client, services, chat["id"], "What is the notice period?")
    client.patch(f"/api/chats/{chat['id']}", json={"visibility": "project"})
    assert client.delete(f"/api/chats/{chat['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}/chats").json() == []
    assert held(client, project["id"]) == [("chat", "What is the notice period?")]

    # Another member sees nothing of it, shared as it was, and cannot bring it back.
    act_as(make_account("max@example.com", account.organization, role="member"))
    assert client.get(f"/api/projects/{project['id']}/chats").json() == []
    assert held(client, project["id"]) == []
    assert restore(client, "chats", chat["id"]).status_code == 404
    assert client.delete(f"/api/trash/chats/{chat['id']}").status_code == 404

    client.app.dependency_overrides.clear()
    assert restore(client, "chats", chat["id"]).status_code == 200
    messages = client.get(f"/api/chats/{chat['id']}").json()["messages"]
    assert [m["content"] for m in messages] == ["What is the notice period?", "Ninety days."]
    client.delete(f"/api/chats/{chat['id']}")
    assert client.delete(f"/api/trash/chats/{chat['id']}").status_code == 204
    assert session.scalar(select(func.count()).select_from(Chat)) == 0
    chat_rows = [action for action, _ in actions(session) if action.startswith("chat_")]
    assert chat_rows[-4:] == ["chat_deleted", "chat_restored", "chat_deleted", "chat_purged"]


def test_a_viewer_reads_the_trash_and_changes_nothing_in_it(
    client, services, account, make_account, act_as, sample_pdf
):
    project = create_project(client)
    (msa,) = upload(client, project["id"], sample_pdf)["documents"]
    client.delete(f"/api/documents/{msa['id']}")
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert held(client, project["id"]) == [("document", "msa.pdf")]
    assert restore(client, "documents", msa["id"]).status_code == 403
    assert client.delete(f"/api/trash/documents/{msa['id']}").status_code == 403
    assert client.delete(f"/api/projects/{project['id']}/trash").status_code == 403


def test_an_answer_under_way_finishes_in_the_trash(client, services, indexed):
    chat = new_chat(client, indexed.project_id)
    services.llm.agent_turns.append(FakeTurn(TextPart("Ninety days.")))
    asked = ask(client, chat["id"], "What is the notice period?")
    assert client.delete(f"/api/chats/{chat['id']}").status_code == 204
    answer(asked["answer"]["id"], services)
    assert restore(client, "chats", chat["id"]).status_code == 200
    messages = client.get(f"/api/chats/{chat['id']}").json()["messages"]
    assert [(m["content"], m["status"]) for m in messages][-1] == ("Ninety days.", "done")


def test_the_trash_lists_its_newest_and_says_how_much_it_holds(client, monkeypatch):
    monkeypatch.setattr("app.trash.SHOWN", 2)
    project = create_project(client)
    for name in ("One", "Two", "Three"):
        folder = make_folder(client, project["id"], name)
        client.delete(f"/api/folders/{folder['id']}")
    chat = new_chat(client, project["id"])
    client.delete(f"/api/chats/{chat['id']}")
    listing = trash(client, project["id"])
    assert [(item["kind"], item["name"]) for item in listing["items"]] == [
        ("chat", ""),
        ("folder", "Three"),
    ]
    assert listing["total"] == 4
