"""A project's filing rules (app/filing.py, app/api/filing.py): written whole by whoever may change
the project, each change audited; and the filing itself, once, at the end of the first indexing
that read a document's type and facts, which moves a document that arrived in the root by the
first rule that moves, and tags it by every rule that tags, wherever it arrived."""

from sqlalchemy import text

from app.api.filing import NO_FIELD, NO_FOLDER, NO_TAG, NO_TYPE, NOTHING_TO_HOLD, TAG_ONLY
from app.filing import FOLDER_TRASHED, TOO_MANY
from app.indexing import pipeline
from app.indexing.enrich import ExtractedFact
from app.indexing.pipeline import process_document
from app.jobs import claim
from app.models import Connector, ConnectorFile, Document
from app.tags import NOT_THE_PROJECTS
from app.worker import HANDLERS
from tests.test_api import create_project, upload
from tests.test_audit import actions
from tests.test_folders import make_folder, upload_into
from tests.test_library import files
from tests.test_tags import make_tag

CONTRACTS = {"condition": "type", "value": "Contract", "action": "folder"}


def make_rule(client, project_id, **rule):
    response = client.post(f"/api/projects/{project_id}/filing-rules", json=rule)
    assert response.status_code == 201, response.text
    return response.json()


def arrive(client, services, project_id, directory, name, folder_id=None):
    """A text file uploaded and indexed: the document as it is then."""
    paths = files(directory, [name])
    sent = (
        upload(client, project_id, *paths)
        if folder_id is None
        else upload_into(client, project_id, folder_id, *paths)
    )
    (document,) = sent["documents"]
    process_document(document["id"], services)
    return client.get(f"/api/documents/{document['id']}").json()


def path_of(client, project_id, document) -> str:
    folders = {f["id"]: f for f in client.get(f"/api/projects/{project_id}/folders").json()}
    parts, folder_id = [], document["folder_id"]
    while folder_id is not None:
        parts.insert(0, folders[folder_id]["name"])
        folder_id = folders[folder_id]["parent_id"]
    return "/".join([*parts, document["filename"]])


def test_a_new_document_in_the_root_is_moved_by_its_type(client, services, session, tmp_path):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    elsewhere = make_folder(client, project["id"], "Elsewhere")
    signed = make_tag(client, project["id"], "Signed")
    rule = make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    # The model writes a type in lowercase, so the rule looks for it so.
    assert rule["value"] == "contract" and rule["folder"] == "Contracts"
    make_rule(client, project["id"], condition="any", action="tag", tag_id=signed["id"])

    # The fake model reads every document as a contract dated 1 March 2024.
    moved = arrive(client, services, project["id"], tmp_path / "a", "msa.txt")
    assert path_of(client, project["id"], moved) == "Contracts/msa.txt"
    assert moved["tag_ids"] == [signed["id"]]
    row = session.get(Document, moved["id"])
    session.refresh(row)
    assert row.filed_at is not None

    # Uploaded into a folder, a document stays where it was put, and is tagged all the same.
    kept = arrive(client, services, project["id"], tmp_path / "b", "nda.txt", elsewhere["id"])
    assert path_of(client, project["id"], kept) == "Elsewhere/nda.txt"
    assert kept["tag_ids"] == [signed["id"]]


def test_a_dated_rule_files_by_year_and_month_making_the_folders_once(client, services, tmp_path):
    project = create_project(client)
    invoices = make_folder(client, project["id"], "Invoices")
    make_rule(client, project["id"], condition="any", action="by_month", folder_id=invoices["id"])
    first = arrive(client, services, project["id"], tmp_path / "a", "a.txt")
    second = arrive(client, services, project["id"], tmp_path / "b", "b.txt")
    assert path_of(client, project["id"], first) == "Invoices/2024/03/a.txt"
    assert path_of(client, project["id"], second) == "Invoices/2024/03/b.txt"
    folders = client.get(f"/api/projects/{project['id']}/folders").json()
    assert sorted(f["name"] for f in folders) == ["03", "2024", "Invoices"]

    # By year, below the root.
    other = create_project(client, "By year")
    make_rule(client, other["id"], condition="any", action="by_year")
    filed = arrive(client, services, other["id"], tmp_path / "c", "c.txt")
    assert path_of(client, other["id"], filed) == "2024/c.txt"


def test_a_fact_rule_reads_the_words_of_the_fact_s_value(client, services, tmp_path):
    project = create_project(client)
    acme = make_folder(client, project["id"], "Acme")
    globex = make_folder(client, project["id"], "Globex")
    services.llm.more_facts = [
        ExtractedFact(
            name="Supplier",
            value="ACME Corporation Ltd",
            type="text",
            number=None,
            date=None,
            currency=None,
            block=0,
            quote="ACME",
        )
    ]
    by_supplier = {"condition": "fact", "field": "supplier", "action": "folder"}
    make_rule(client, project["id"], **by_supplier, value="globex", folder_id=globex["id"])
    make_rule(client, project["id"], **by_supplier, value="acme corp", folder_id=acme["id"])
    filed = arrive(client, services, project["id"], tmp_path, "invoice.txt")
    assert path_of(client, project["id"], filed) == "Acme/invoice.txt"


def test_the_first_rule_that_moves_moves_and_a_rule_turned_off_files_nothing(
    client, services, tmp_path
):
    project = create_project(client)
    first = make_folder(client, project["id"], "First")
    second = make_folder(client, project["id"], "Second")
    off = make_folder(client, project["id"], "Off")
    make_rule(client, project["id"], **CONTRACTS, folder_id=off["id"], enabled=False)
    make_rule(
        client,
        project["id"],
        condition="type",
        value="invoice",
        action="folder",
        folder_id=off["id"],
    )
    make_rule(client, project["id"], **CONTRACTS, folder_id=first["id"])
    make_rule(client, project["id"], condition="any", action="folder", folder_id=second["id"])
    filed = arrive(client, services, project["id"], tmp_path, "msa.txt")
    assert path_of(client, project["id"], filed) == "First/msa.txt"


def test_a_document_is_filed_once(client, services, tmp_path):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    filed = arrive(client, services, project["id"], tmp_path, "msa.txt")
    assert filed["folder_id"] == contracts["id"]

    # Moved back to the root by a member and read again, it stays where the member put it.
    client.patch(f"/api/documents/{filed['id']}", json={"folder_id": None})
    client.post(f"/api/documents/{filed['id']}/reprocess")
    process_document(filed["id"], services)
    assert client.get(f"/api/documents/{filed['id']}").json()["folder_id"] is None


def test_a_document_read_without_its_summaries_waits_for_them(client, services, tmp_path):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    services.llm.available = False
    waiting = arrive(client, services, project["id"], tmp_path, "msa.txt")
    assert waiting["stages"]["enrich"]["status"] == "skipped" and waiting["folder_id"] is None

    # Its rest indexed, it is filed then.
    services.llm.available = True
    client.post(f"/api/documents/{waiting['id']}/reprocess", params={"from_stage": "enrich"})
    process_document(waiting["id"], services)
    assert client.get(f"/api/documents/{waiting['id']}").json()["folder_id"] == contracts["id"]


def test_a_document_is_filed_in_the_commit_that_makes_it_ready(
    client, services, tmp_path, monkeypatch
):
    """Whoever sees the document ready sees it where it was filed: the library stops asking
    about a folder once nothing in it is being read, so a move after would go unseen."""
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    (document,) = upload(client, project["id"], *files(tmp_path, ["msa.txt"]))["documents"]
    seen = []
    committed = pipeline._commit

    def watched(session, lease):
        committed(session, lease)
        found = text("SELECT status, folder_id FROM documents WHERE id = :id")
        seen.append(tuple(session.execute(found, {"id": document["id"]}).one()))

    monkeypatch.setattr(pipeline, "_commit", watched)
    HANDLERS["index_document"].run(claim("w:1", ["index_document"]), services)
    assert ("ready", contracts["id"]) in seen
    assert ("ready", None) not in seen


def test_a_document_of_a_connected_folder_stays_where_its_sync_put_it(
    client, services, session, account, tmp_path
):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    signed = make_tag(client, project["id"], "Signed")
    make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    make_rule(client, project["id"], condition="any", action="tag", tag_id=signed["id"])
    (document,) = upload(client, project["id"], *files(tmp_path, ["synced.txt"]))["documents"]
    connector = Connector(
        org_id=account.organization.id,
        project_id=project["id"],
        provider="google",
        user_id=account.user.id,
        account="owner@example.com",
        refresh_token="",
        folder_ref="folder-1",
        folder_name="Shared",
        status="active",
    )
    session.add(connector)
    session.flush()
    session.add(
        ConnectorFile(
            connector_id=connector.id,
            remote_id="file-1",
            version="1",
            path="synced.txt",
            document_id=document["id"],
        )
    )
    session.commit()
    process_document(document["id"], services)
    kept = client.get(f"/api/documents/{document['id']}").json()
    assert kept["folder_id"] is None and kept["tag_ids"] == [signed["id"]]


def test_a_move_takes_a_name_no_document_there_has(client, services, tmp_path):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    upload_into(client, project["id"], contracts["id"], *files(tmp_path / "a", ["msa.txt"]))
    make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    filed = arrive(client, services, project["id"], tmp_path / "b", "MSA.txt")
    assert path_of(client, project["id"], filed) == "Contracts/MSA (2).txt"


def test_a_rule_whose_folder_is_in_the_trash_files_nothing_there(client, services, tmp_path):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    rule = make_rule(client, project["id"], **CONTRACTS, folder_id=contracts["id"])
    client.delete(f"/api/folders/{contracts['id']}")
    (listed,) = client.get(f"/api/projects/{project['id']}/filing-rules").json()["rules"]
    assert listed["id"] == rule["id"] and listed["problem"] == FOLDER_TRASHED
    stayed = arrive(client, services, project["id"], tmp_path, "msa.txt")
    assert stayed["folder_id"] is None
    # Deleted for good, the folder takes its rules with it.
    client.delete(f"/api/trash/folders/{contracts['id']}")
    assert client.get(f"/api/projects/{project['id']}/filing-rules").json()["rules"] == []


def test_a_rule_is_written_whole_and_each_change_is_audited(client, session):
    project = create_project(client)
    other = create_project(client, "Other")
    contracts = make_folder(client, project["id"], "Contracts")
    theirs = make_folder(client, other["id"], "Theirs")
    their_tag = make_tag(client, other["id"], "Theirs")
    urgent = make_tag(client, project["id"], "Urgent")
    url = f"/api/projects/{project['id']}/filing-rules"

    def refused(status, detail=None, **rule):
        response = client.post(url, json=rule)
        assert response.status_code == status, (rule, response.text)
        if detail is not None:
            assert response.json()["detail"] == detail

    refused(400, NO_TYPE, condition="type", action="by_year")
    refused(400, NO_FIELD, condition="fact", value="acme", action="by_year")
    refused(400, NOTHING_TO_HOLD, condition="any", value="acme", action="by_year")
    refused(400, NO_FOLDER, condition="any", action="folder")
    refused(400, NO_TAG, condition="any", action="tag")
    refused(
        400, TAG_ONLY, condition="any", action="tag", tag_id=urgent["id"], folder_id=contracts["id"]
    )
    refused(404, None, condition="any", action="folder", folder_id=theirs["id"])
    refused(404, NOT_THE_PROJECTS, condition="any", action="tag", tag_id=their_tag["id"])
    refused(422, None, condition="colour", action="tag", tag_id=urgent["id"])

    rule = make_rule(
        client,
        project["id"],
        condition="fact",
        field="supplier",
        value="Acme",
        action="tag",
        tag_id=urgent["id"],
    )
    assert (rule["tag"], rule["made_by"], rule["problem"]) == ("Urgent", "Owner", None)
    unchanged = client.put(f"/api/filing-rules/{rule['id']}", json={**rule})
    assert unchanged.status_code == 200
    moved = client.put(
        f"/api/filing-rules/{rule['id']}",
        json={
            "condition": "any",
            "action": "by_year",
            "folder_id": contracts["id"],
            "enabled": False,
        },
    )
    assert moved.status_code == 200 and moved.json()["folder"] == "Contracts"
    assert client.delete(f"/api/filing-rules/{rule['id']}").status_code == 204
    logged = [(action, detail) for action, detail in actions(session) if "filing_rule" in action]
    # Never the words a fact's value holds, which may be a document's.
    assert logged == [
        (
            "filing_rule_created",
            {
                "condition": "fact",
                "type": None,
                "field": "supplier",
                "action": "tag",
                "folder": None,
                "tag": "Urgent",
                "enabled": True,
            },
        ),
        (
            "filing_rule_changed",
            {
                "condition": "any",
                "type": None,
                "field": None,
                "action": "by_year",
                "folder": "Contracts",
                "tag": None,
                "enabled": False,
                "changed": ["condition", "action", "enabled"],
            },
        ),
        (
            "filing_rule_deleted",
            {
                "condition": "any",
                "type": None,
                "field": None,
                "action": "by_year",
                "folder": "Contracts",
                "tag": None,
                "enabled": False,
            },
        ),
    ]


def test_a_project_has_at_most_so_many_rules(client, monkeypatch):
    monkeypatch.setattr("app.api.filing.MAX_FILING_RULES", 1)
    project = create_project(client)
    make_rule(client, project["id"], condition="any", action="by_year")
    refused = client.post(
        f"/api/projects/{project['id']}/filing-rules",
        json={"condition": "any", "action": "by_year"},
    )
    assert refused.status_code == 409 and refused.json()["detail"] == TOO_MANY
    listed = client.get(f"/api/projects/{project['id']}/filing-rules").json()
    assert listed["max_filing_rules"] == 1
