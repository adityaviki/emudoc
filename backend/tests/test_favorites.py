"""Each reader's favourites and recent documents (app/api/favorites.py, app/recents.py): their
own, a viewer's too, listed by the library, kept in their account export, and gone with the
document; the recent ones the newest RECENT a project, the older let go."""

from sqlalchemy import func, select, text

from app.export import account_export
from app.models import DocumentFavorite, DocumentVisit, User
from app.recents import RECENT
from tests.test_api import create_project, upload
from tests.test_folders import make_folder, upload_into
from tests.test_library import files


def star(client, document_id):
    assert client.put(f"/api/documents/{document_id}/favorite").status_code == 204


def unstar(client, document_id):
    assert client.delete(f"/api/documents/{document_id}/favorite").status_code == 204


def visit(client, document_id):
    assert client.put(f"/api/documents/{document_id}/visit").status_code == 204


def favorites(client, project_id) -> list[int]:
    return client.get(f"/api/projects/{project_id}/favorites").json()["document_ids"]


def place(client, project_id, **params) -> list[str]:
    response = client.get(f"/api/projects/{project_id}/documents", params=params)
    assert response.status_code == 200, response.text
    return [document["filename"] for document in response.json()["documents"]]


def test_a_reader_s_favourites_are_theirs_wherever_the_documents_are(
    client, session, tmp_path, account, make_account, act_as
):
    project = create_project(client, "Stars")
    folder = make_folder(client, project["id"], "Inside")
    a, b = upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt"]))["documents"]
    (c,) = upload_into(client, project["id"], folder["id"], *files(tmp_path / "c", ["c.txt"]))[
        "documents"
    ]
    star(client, b["id"])
    star(client, c["id"])
    star(client, a["id"])
    # Starred again, a document keeps its place.
    star(client, b["id"])
    assert favorites(client, project["id"]) == [a["id"], c["id"], b["id"]]
    assert place(client, project["id"], favorites=True) == ["a.txt", "b.txt", "c.txt"]
    assert place(client, project["id"], favorites=True, sort="added", order="desc") == [
        "c.txt",
        "b.txt",
        "a.txt",
    ]
    unstar(client, c["id"])
    unstar(client, c["id"])
    assert favorites(client, project["id"]) == [a["id"], b["id"]]

    # A viewer stars for themselves; nobody else sees it.
    viewer = make_account("vic@example.com", account.organization, role="viewer")
    act_as(viewer)
    assert favorites(client, project["id"]) == []
    star(client, c["id"])
    assert place(client, project["id"], favorites=True) == ["c.txt"]
    exported = account_export(session, session.get(User, viewer.user.id)).favorite_documents
    assert [(f.project, f.document) for f in exported] == [("Stars", "c.txt")]

    # In the trash, a favourite waits with its document; deleted for good, it goes.
    client.app.dependency_overrides.clear()
    client.delete(f"/api/documents/{a['id']}")
    assert favorites(client, project["id"]) == [b["id"]]
    assert place(client, project["id"], favorites=True) == ["b.txt"]
    client.post(f"/api/trash/documents/{a['id']}/restore")
    assert favorites(client, project["id"]) == [a["id"], b["id"]]
    client.delete(f"/api/documents/{a['id']}")
    assert client.delete(f"/api/trash/documents/{a['id']}").status_code == 204
    held = select(func.count()).select_from(DocumentFavorite)
    assert session.scalar(held.where(DocumentFavorite.document_id == a["id"])) == 0


def test_a_reader_s_recent_documents_are_the_newest_they_opened(
    client, session, tmp_path, account, make_account, act_as
):
    project = create_project(client, "Recents")
    other = create_project(client, "Other")
    names = [f"{number:02d}.txt" for number in range(RECENT + 2)]
    documents = upload(client, project["id"], *files(tmp_path, names))["documents"]
    (theirs,) = upload(client, other["id"], *files(tmp_path / "o", ["other.txt"]))["documents"]
    # Each visit a moment after the one before, as a member opens one after another.
    for document in documents:
        visit(client, document["id"])
        session.execute(
            text("UPDATE document_visits SET opened_at = opened_at - interval '1 minute'")
        )
        session.commit()
    visit(client, theirs["id"])
    visit(client, documents[3]["id"])

    # The newest first, on one page whatever the order asked for, the oldest let go.
    newest = ["03.txt", *reversed(names[4:]), "02.txt"]
    assert place(client, project["id"], recent=True) == newest
    assert place(client, project["id"], recent=True, sort="size", limit=5) == newest
    mine = select(func.count()).select_from(DocumentVisit)
    assert session.scalar(mine.where(DocumentVisit.user_id == account.user.id)) == RECENT + 1
    # Another project's are its own.
    assert place(client, other["id"], recent=True) == ["other.txt"]

    # Another member's are theirs, and their export holds them.
    max_ = make_account("max@example.com", account.organization, role="member")
    act_as(max_)
    assert place(client, project["id"], recent=True) == []
    visit(client, documents[0]["id"])
    assert place(client, project["id"], recent=True) == ["00.txt"]
    exported = account_export(session, session.get(User, max_.user.id)).recent_documents
    assert [(v.project, v.document) for v in exported] == [("Recents", "00.txt")]
    client.app.dependency_overrides.clear()
    # In the trash, a document is in nobody's recent ones.
    client.delete(f"/api/documents/{documents[3]['id']}")
    assert place(client, project["id"], recent=True)[0] == names[-1]
