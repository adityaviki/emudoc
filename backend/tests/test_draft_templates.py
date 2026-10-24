"""Draft templates (app/draft_templates.py, the routes in app/api/drafts.py): how a project's
drafts of each kind read, the built-in template or the project's own in its place, which the
agent is told after the rules every draft keeps."""

from pydantic_ai.messages import TextPart

from app.draft_templates import BUILT_IN, KIND_FORMS, OWN_TEMPLATE, kind_task
from app.drafts import DRAFT_TASK
from app.models import DRAFT_KINDS
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_drafts import LETTER, ask, disputed, flags_on, write  # noqa: F401 (fixture)

HOUSE_STYLE = {
    "tone": "  Formal, in British English.  ",
    "sections": [
        {"heading": "Our reference", "description": "The order number we gave.", "required": False},
        {"heading": "What is disputed", "description": "Each figure beside what it should be."},
        {"heading": "Closing", "description": "Signed by [Your name], Accounts Payable."},
    ],
    "guidance": "Quote the contract's clause for each figure.",
}


def templates(client, project_id: int) -> dict[str, dict]:
    response = client.get(f"/api/projects/{project_id}/draft-templates")
    assert response.status_code == 200, response.text
    return {template["kind"]: template for template in response.json()["templates"]}


def save(client, project_id: int, kind: str, body: dict):
    return client.put(f"/api/projects/{project_id}/draft-templates/{kind}", json=body)


def logged(session) -> list[tuple[str, dict]]:
    return [(action, detail) for action, detail in actions(session) if "draft_template" in action]


def test_each_kind_has_a_built_in_template_until_the_project_writes_its_own(client, session):
    project = create_project(client)
    listed = templates(client, project["id"])
    assert tuple(listed) == DRAFT_KINDS
    assert all(template["built_in"] and template["id"] is None for template in listed.values())
    letter = listed["dispute_letter"]
    assert letter["tone"] == "Firm and factual, never accusing."
    assert [section["heading"] for section in letter["sections"]] == [
        "Sender and recipient",
        "Date",
        "Subject",
        "What is disputed",
        "What is asked",
        "Closing",
    ]

    saved = save(client, project["id"], "dispute_letter", HOUSE_STYLE)
    assert saved.status_code == 200, saved.text
    own = saved.json()
    assert (own["built_in"], own["tone"], own["guidance"]) == (
        False,
        "Formal, in British English.",
        "Quote the contract's clause for each figure.",
    )
    assert own["sections"][0] == {
        "heading": "Our reference",
        "description": "The order number we gave.",
        "required": False,
    }
    assert own["sections"][1]["required"] is True and own["updated_at"] is not None
    listed = templates(client, project["id"])
    assert listed["dispute_letter"] == own
    assert all(listed[kind]["built_in"] for kind in DRAFT_KINDS if kind != "dispute_letter")
    # Another project keeps the built-in one.
    assert templates(client, create_project(client, "Other")["id"])["dispute_letter"]["built_in"]
    assert logged(session) == [
        (
            "draft_template_created",
            {
                "kind": "dispute_letter",
                "sections": ["Our reference", "What is disputed", "Closing"],
            },
        )
    ]


def test_a_draft_is_written_to_the_template_in_force_as_its_writing_starts(
    client, session, services
):
    project = create_project(client)
    docs = disputed(client, session, services, project["id"])
    assert save(client, project["id"], "dispute_letter", HOUSE_STYLE).status_code == 200
    services.llm.agent_turns += [FakeTurn(TextPart("Ours.")), FakeTurn(TextPart("Built in."))]
    draft = ask(client, project["id"], **LETTER, document_ids=[docs["INV-1"]])
    write(services)

    prompt = services.llm.agent_prompts[-1]
    task = prompt[prompt.index(DRAFT_TASK) + len(DRAFT_TASK) :]
    assert task == "\n".join(
        [
            KIND_FORMS["dispute_letter"],
            OWN_TEMPLATE,
            "Its tone: Formal, in British English.",
            "Its parts, in this order:",
            "- Our reference (only where the documents give it something): The order number we "
            "gave.",
            "- What is disputed: Each figure beside what it should be.",
            "- Closing: Signed by [Your name], Accounts Payable.",
            "Also: Quote the contract's clause for each figure.",
        ]
    )

    # The built-in one again: the draft written again follows it.
    gone = client.delete(f"/api/projects/{project['id']}/draft-templates/dispute_letter")
    assert gone.status_code == 204
    assert client.post(f"/api/drafts/{draft['id']}/rewrite", json={}).status_code == 200
    write(services)
    assert services.llm.agent_prompts[-1].endswith(
        DRAFT_TASK + kind_task(BUILT_IN["dispute_letter"])
    )
    assert OWN_TEMPLATE not in services.llm.agent_prompts[-1]


def test_a_change_is_recorded_by_what_changed_and_nothing_for_none(client, session):
    project = create_project(client)
    path = f"/api/projects/{project['id']}/draft-templates/reply_email"
    assert save(client, project["id"], "reply_email", HOUSE_STYLE).status_code == 200
    # The same template again changes nothing, and is not recorded.
    assert save(client, project["id"], "reply_email", HOUSE_STYLE).status_code == 200
    changed = {
        **HOUSE_STYLE,
        "tone": "Warm.",
        "sections": [
            {"heading": "Greeting", "description": "Dear [Name],"},
            {"heading": "What is disputed", "description": "Each figure, briefly."},
            {"heading": "Closing", "description": "Signed by [Your name], Accounts Payable."},
        ],
    }
    assert save(client, project["id"], "reply_email", changed).status_code == 200
    assert client.delete(path).status_code == 204
    refused = client.delete(path)
    assert (refused.status_code, refused.json()["detail"]) == (
        404,
        "This project has no template of its own for this kind: the built-in one applies.",
    )
    assert [action for action, _ in logged(session)] == [
        "draft_template_created",
        "draft_template_changed",
        "draft_template_deleted",
    ]
    assert logged(session)[1][1] == {
        "kind": "reply_email",
        "added": ["Greeting"],
        "removed": ["Our reference"],
        "changed": ["What is disputed"],
        "tone": True,
        "guidance": False,
    }
    assert logged(session)[2][1] == {"kind": "reply_email"}


def test_a_template_names_each_part_once_and_is_of_a_kind_there_is(client, session):
    project = create_project(client)
    twice = {"sections": [{"heading": "Date"}, {"heading": " date "}]}
    refused = save(client, project["id"], "report", twice)
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "Two parts are named “date”. Give each part a name of its own.",
    )
    assert save(client, project["id"], "memo", HOUSE_STYLE).status_code == 422
    assert (
        save(client, project["id"], "report", {"sections": [{"heading": " "}]}).status_code == 422
    )
    many = {"sections": [{"heading": f"Part {n}"} for n in range(21)]}
    assert save(client, project["id"], "report", many).status_code == 422
    assert save(client, project["id"], "report", {"tone": "x" * 501}).status_code == 422
    # A template of no parts is the kind's layout and its tone alone.
    bare = save(client, project["id"], "report", {"tone": "Short."})
    assert bare.status_code == 200 and bare.json()["sections"] == []
    assert logged(session) == [("draft_template_created", {"kind": "report", "sections": []})]


def test_the_built_in_tasks_say_each_kinds_layout_and_parts():
    task = kind_task(BUILT_IN["reply_email"])
    assert task.splitlines() == [
        'The draft is a reply email: a line "Subject: …" first, then the reply in short '
        "paragraphs, with no headings.",
        "Its tone: Courteous and plain.",
        "Its parts, in this order:",
        "- Greeting: A greeting to whoever wrote the email it replies to.",
        "- Reply: What the email it replies to asks or says, answered point by point.",
        "- Closing: A closing with [Your name].",
    ]
    for kind in DRAFT_KINDS:
        assert OWN_TEMPLATE not in kind_task(BUILT_IN[kind])
