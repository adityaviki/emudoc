"""The search of every project a member sees (app/api/search.py): one ranking over all of them,
each hit naming its project; a private project searched only by those who see it, another
organisation's and the trash's never; narrowed to some of the projects, to types and to dates;
and the types to narrow by."""

from sqlalchemy import text

from app.api.deps import get_person
from app.apikeys import ELSEWHERE
from app.document_filter import BACKWARDS
from app.indexing.pipeline import process_document
from app.schemas import MAX_SEARCHED_PROJECTS
from app.search import search_projects
from tests.conftest import FakeEmbedder
from tests.test_access import access, crew  # noqa: F401
from tests.test_api import create_project
from tests.test_apikeys import make_key, program
from tests.test_search_filter import set_columns


def write(client, services, project_id, name, words, index=True) -> dict:
    """A text file uploaded into the project, and indexed unless asked not to be."""
    response = client.post(
        f"/api/projects/{project_id}/documents", files=[("files", (name, words.encode()))]
    )
    assert response.status_code == 201, response.text
    (document,) = response.json()["documents"]
    if index:
        process_document(document["id"], services)
    return document


def searched(client, q="delivery", **params):
    return client.get("/api/search", params={"q": q, "limit": 50, **params})


def found(client, q="delivery", **params) -> list[tuple[str, str]]:
    """The documents the passages a search found are in, by their project's name and their file's
    without `.txt`. The projects are small enough for the ranking by meaning to bring every
    passage the filter leaves, so these are the documents it matches."""
    response = searched(client, q, **params)
    assert response.status_code == 200, response.text
    hits = response.json()["hits"]
    named = {
        (hit["project_name"], client.get(f"/api/documents/{hit['document_id']}").json()["filename"])
        for hit in hits
    }
    return sorted((project, filename.removesuffix(".txt")) for project, filename in named)


def test_a_search_finds_what_every_project_the_member_sees_holds_in_one_ranking(
    client,
    services,
    crew,  # noqa: F811
    act_as,
    make_account,
):
    contracts = create_project(client, "Contracts")
    archive = create_project(client, "Archive")
    write(client, services, contracts["id"], "terms.txt", "Late delivery penalty of 5 % a week.")
    write(client, services, archive["id"], "log.txt", "Late delivery is excused by force majeure.")
    old = write(client, services, archive["id"], "old.txt", "A delivery from last year.")

    # One ranking, the best first wherever it is, each hit naming its project.
    response = searched(client, "late delivery penalty")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["semantic"] is True
    hits = result["hits"]
    assert (hits[0]["project_name"], hits[0]["project_id"]) == ("Contracts", contracts["id"])
    assert {(hit["project_id"], hit["project_name"]) for hit in hits} == {
        (contracts["id"], "Contracts"),
        (archive["id"], "Archive"),
    }
    assert [hit["score"] for hit in hits] == sorted((hit["score"] for hit in hits), reverse=True)
    assert any(segment["match"] for segment in hits[0]["snippet"])
    every = [("Archive", "log"), ("Archive", "old"), ("Contracts", "terms")]
    assert found(client) == every

    # A private project is searched by those who see it: its chosen members, the owners and the
    # admins; to anyone else it is not there to choose.
    assert access(client, archive["id"], "private", crew.bob).status_code == 200
    for member, sees in (
        (crew.ann, every),
        (crew.bob, every),
        (crew.cat, [("Contracts", "terms")]),
        (crew.vic, [("Contracts", "terms")]),
    ):
        act_as(member)
        assert found(client) == sees, member.user.email
    assert searched(client, projects=[archive["id"]]).status_code == 404

    # Another organisation's projects are its own members' alone.
    eve = make_account("eve@elsewhere.example")
    act_as(eve)
    theirs = create_project(client, "Theirs")
    write(client, services, theirs["id"], "far.txt", "Late delivery elsewhere.")
    assert found(client) == [("Theirs", "far")]
    act_as(crew.owner)
    assert found(client) == every
    assert searched(client, projects=[theirs["id"]]).status_code == 404

    # The trash is searched for nothing: a document in it, nor a project.
    assert client.delete(f"/api/documents/{old['id']}").status_code == 204
    assert found(client) == [("Archive", "log"), ("Contracts", "terms")]
    assert client.delete(f"/api/projects/{archive['id']}").status_code == 204
    assert found(client) == [("Contracts", "terms")]
    assert searched(client, projects=[archive["id"]]).status_code == 404

    # The page's alone: a program searches one project through the versioned API.
    client.app.dependency_overrides.pop(get_person)
    api = program(client, make_key(client)["key"])
    refused = api.get("/api/search", params={"q": "delivery"})
    assert (refused.status_code, refused.json()["detail"]) == (403, ELSEWHERE)


def test_a_search_across_projects_is_narrowed_to_some_of_them_their_types_and_dates(
    client, services, session
):
    contracts = create_project(client, "Contracts")
    archive = create_project(client, "Archive")
    a = write(client, services, contracts["id"], "a.txt", "Late delivery penalty of 5 % a week.")
    b = write(client, services, contracts["id"], "b.txt", "Late delivery excused by force majeure.")
    c = write(client, services, archive["id"], "c.txt", "The delivery penalty applies to invoices.")
    d = write(client, services, archive["id"], "d.txt", "Delivery and payment within thirty days.")
    set_columns(session, a, doc_type="invoice", doc_date="2024-01-10")
    set_columns(session, b, doc_type="contract", doc_date="2024-06-01")
    set_columns(session, c, doc_type="invoice", doc_date="2023-12-31")
    # Undated, whatever the model read.
    set_columns(session, d, doc_type="invoice", doc_date=None)
    a, b = ("Contracts", "a"), ("Contracts", "b")
    c, d = ("Archive", "c"), ("Archive", "d")

    assert found(client) == [c, d, a, b]
    assert found(client, projects=[archive["id"]]) == [c, d]
    assert found(client, projects=[archive["id"], contracts["id"]]) == [c, d, a, b]
    assert found(client, types=["Invoice"]) == [c, d, a]
    assert found(client, after="2024-01-01") == [a, b]
    assert found(client, before="2024-01-10", types=["invoice"]) == [c, a]
    assert found(client, before="2024-01-10", projects=[contracts["id"]]) == [a]
    # A filter that matches nothing searches nothing, rather than everything.
    assert found(client, types=["letter"]) == []

    backwards = searched(client, after="2024-02-01", before="2024-01-01")
    assert backwards.status_code == 400 and backwards.json()["detail"] == BACKWARDS
    assert searched(client, before="2024-01").status_code == 422
    too_many = list(range(1, MAX_SEARCHED_PROJECTS + 2))
    assert searched(client, projects=too_many).status_code == 422
    assert searched(client, limit=51).status_code == 422
    # Blank words find nothing.
    assert searched(client, "   ").json() == {"hits": [], "semantic": True}


def test_without_the_embeddings_a_search_across_projects_matches_keywords_and_says_so(
    client, services, session, caplog
):
    for name in ("Contracts", "Archive"):
        project = create_project(client, name)
        write(client, services, project["id"], "terms.txt", f"The {name} penalty clause.")
    services.embedder.fail = True
    with caplog.at_level("WARNING", logger="app.search"):
        result = searched(client, "penalty").json()
    assert result["semantic"] is False
    assert {hit["project_name"] for hit in result["hits"]} == {"Contracts", "Archive"}
    assert all(hit["vector_rank"] is None for hit in result["hits"])
    # The operator hears of it, without the words.
    assert [record.getMessage() for record in caplog.records if record.name == "app.search"] == [
        f"A search in 2 projects matched keywords only: {FakeEmbedder.UNREACHABLE}"
    ]


def test_each_search_across_projects_is_planned_for_its_projects(client, services, session):
    # As one project's search (test_search.py): a plan kept for every set of projects would be
    # made for none of them.
    ids = []
    for name in ("Contracts", "Archive"):
        project = create_project(client, name)
        write(client, services, project["id"], "terms.txt", "The termination notice.")
        ids.append(project["id"])
    for _ in range(15):
        assert search_projects(session, services.embedder, ids, "termination notice").hits
    held = session.scalar(
        text(
            "SELECT coalesce(sum(generic_plans), 0) FROM pg_prepared_statements"
            " WHERE statement LIKE '%WITH langs AS%'"
        )
    )
    assert held == 0


def test_the_types_to_narrow_by_are_those_of_every_project_the_member_sees(
    client,
    services,
    session,
    crew,  # noqa: F811
    act_as,
):
    contracts = create_project(client, "Contracts")
    archive = create_project(client, "Archive")
    typed = {
        "a.txt": (contracts, "invoice"),
        "b.txt": (archive, "invoice"),
        "c.txt": (archive, "letter"),
        "d.txt": (archive, "memo"),
        "e.txt": (archive, None),
    }
    made = {}
    for name, (project, doc_type) in typed.items():
        made[name] = write(client, services, project["id"], name, f"Words of {name}.", index=False)
        set_columns(session, made[name], doc_type=doc_type)
    assert client.delete(f"/api/documents/{made['d.txt']['id']}").status_code == 204

    def types():
        response = client.get("/api/document-types")
        assert response.status_code == 200, response.text
        return [(one["type"], one["count"]) for one in response.json()]

    assert types() == [("invoice", 2), ("letter", 1)]
    assert access(client, archive["id"], "private", crew.bob).status_code == 200
    act_as(crew.cat)
    assert types() == [("invoice", 1)]
