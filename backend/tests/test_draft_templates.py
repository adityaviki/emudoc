"""Writing templates (app/draft_templates.py, the routes in app/api/draft_templates.py): how the
documents of each kind the agent writes into a member's My files read for a project, the built-in
template or the project's own in its place, which the agent is told after the rules every such
document keeps, by `get_template` in a chat."""

from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart

from app.agent.loop import WRITING
from app.draft_templates import (
    BUILT_IN,
    KIND_FORMS,
    OWN_TEMPLATE,
    TEMPLATE_RULES,
    kind_task,
)
from app.models import DRAFT_KINDS
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_writing import asked

HOUSE_STYLE = {
    "tone": "  Formal, in British English.  ",
    "sections": [
        {"heading": "Our reference", "description": "The order number we gave.", "required": False},
        {"heading": "What is disputed", "description": "Each figure beside what it should be."},
        {"heading": "Closing", "description": "Signed by [Your name], Accounts Payable."},
    ],
    "guidance": "Quote the contract's clause for each figure.",
}


# What the agent is told of a dispute letter to HOUSE_STYLE.
HOUSE_TASK = "\n".join(
    [
        KIND_FORMS["dispute_letter"],
        OWN_TEMPLATE,
        "Its tone: Formal, in British English.",
        "Its parts, in this order:",
        "- Our reference (only where the documents give it something): The order number we gave.",
        "- What is disputed: Each figure beside what it should be.",
        "- Closing: Signed by [Your name], Accounts Payable.",
        "Also: Quote the contract's clause for each figure.",
    ]
)


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
        'It is a reply email: a line "Subject: …" first, then the reply in short paragraphs, '
        "with no headings.",
        "Its tone: Courteous and plain.",
        "Its parts, in this order:",
        "- Greeting: A greeting to whoever wrote the email it replies to.",
        "- Reply: What the email it replies to asks or says, answered point by point.",
        "- Closing: A closing with [Your name].",
    ]
    for kind in DRAFT_KINDS:
        assert OWN_TEMPLATE not in kind_task(BUILT_IN[kind])


def returned(services) -> ToolReturnPart:
    """The tool's result the last turn was sent."""
    (result,) = [
        part
        for part in services.llm.agent_requests[-1][-1].parts
        if isinstance(part, ToolReturnPart)
    ]
    return result


def test_an_answer_reads_the_projects_template_before_it_writes_one(client, session, services):
    project = create_project(client)
    assert save(client, project["id"], "dispute_letter", HOUSE_STYLE).status_code == 200
    asked(
        client,
        services,
        project["id"],
        "Write a dispute letter to Acme about INV-1.",
        FakeTurn(ToolCallPart("get_template", {"kind": "dispute_letter"}, "template-1")),
        FakeTurn(TextPart("I will write it to the project's template.")),
    )
    # Told to read it for these kinds, and offered the tool with the other file tools.
    assert "call get_template for its kind first" in WRITING
    assert WRITING in services.llm.agent_prompts[0]
    assert "get_template" in services.llm.agent_tools[0]
    result = returned(services)
    # The project's own template, after the rules it cannot change, and outside the data tags:
    # it is what the project's members ask, not what a document says.
    own = templates(client, project["id"])["dispute_letter"]
    assert own["built_in"] is False
    assert result.content.startswith(TEMPLATE_RULES)
    assert "Its tone: Formal, in British English." in result.content
    assert "<project_data>" not in result.content
    assert result.content == f"{TEMPLATE_RULES}\n\n{HOUSE_TASK}"

    # The built-in one again: the next answer reads the template in force as it runs.
    gone = client.delete(f"/api/projects/{project['id']}/draft-templates/dispute_letter")
    assert gone.status_code == 204
    asked(
        client,
        services,
        project["id"],
        "Write a dispute letter to Acme about INV-2.",
        FakeTurn(ToolCallPart("get_template", {"kind": "dispute_letter"}, "template-2")),
        FakeTurn(TextPart("I will write it to the built-in template.")),
    )
    result = returned(services)
    assert result.content == f"{TEMPLATE_RULES}\n\n{kind_task(BUILT_IN['dispute_letter'])}"
    assert OWN_TEMPLATE not in result.content
