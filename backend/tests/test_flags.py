"""Feature flags (app/flags.py): a feature on for the share of organisations its flag is rolled
out to and for those an operator turned it on for, read through the organisation by the API, the
page and the worker, and hidden where it is off."""

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import NOT_FOUND, flagged
from app.flags import (
    FLAGS,
    NAME,
    Flag,
    UnknownFlag,
    choose,
    flag_on,
    flag_on_for,
    flags_on,
    in_share,
    rollouts,
)
from app.maintenance import main
from app.models import Organization
from tests.test_audit import actions


def registered(monkeypatch, name: str = "early", share: int = 0) -> str:
    """A flag of the test's own in the registry, which holds none of its own yet."""
    monkeypatch.setitem(FLAGS, name, Flag(f"{name.title()} access", share=share))
    return name


def test_every_flag_is_named_and_shared_as_a_flag_must_be():
    for name, flag in FLAGS.items():
        assert NAME.match(name), name
        assert isinstance(flag, Flag) and flag.title, name
    for share in (-1, 101, 2.5, True):
        with pytest.raises(ValueError, match="percentage from 0 to 100"):
            Flag("Wrong", share=share)


def test_a_share_takes_the_same_organisations_as_it_grows():
    ids = range(1, 2001)
    taken = {share: {i for i in ids if in_share("early", i, share)} for share in (0, 30, 60, 100)}
    assert taken[0] == set() and taken[100] == set(ids)
    assert 500 < len(taken[30]) < 700 and 1100 < len(taken[60]) < 1300
    # Raised, it keeps those it took and adds others.
    assert taken[30] < taken[60]
    # Another flag at the same share takes other organisations.
    other = {i for i in ids if in_share("later", i, 30)}
    assert len(other & taken[30]) < 0.5 * len(taken[30])


def test_an_operators_choice_wins_over_the_share(session, make_account, monkeypatch):
    early = registered(monkeypatch)
    organization = make_account("ann@globex.example").organization
    assert not flag_on(organization, early)

    organization.flags = {early: True}
    assert flag_on(organization, early) and flags_on(organization) == [early]
    FLAGS[early] = Flag("Early access", share=100)
    organization.flags = {early: False}
    assert not flag_on(organization, early)
    # A choice that is no choice, or one for a flag no longer in the registry, is read by nothing.
    organization.flags = {early: "yes", "retired": True}
    assert flag_on(organization, early) and flags_on(organization) == [early]


def test_a_name_the_registry_does_not_hold_is_refused(session, make_account):
    organization = make_account("ann@globex.example").organization
    with pytest.raises(UnknownFlag, match="No feature flag is named 'erly'"):
        flag_on(organization, "erly")
    with pytest.raises(UnknownFlag):
        choose(session, organization, "erly", True)
    # A route behind a misspelt flag fails as its module is imported, not at its first request.
    with pytest.raises(UnknownFlag):
        flagged("erly")


def test_a_choice_is_a_row_of_the_organisations_audit_log(session, make_account, monkeypatch):
    early = registered(monkeypatch, share=100)
    organization = make_account("ann@globex.example").organization

    assert choose(session, organization, early, False, operator=7)
    assert not choose(session, organization, early, False)
    assert choose(session, organization, early, None)
    session.commit()
    assert organization.flags == {}
    assert actions(session, organization.id) == [
        ("flag_changed", {"flag": early, "from": None, "to": False, "on": False, "operator": 7}),
        ("flag_changed", {"flag": early, "from": False, "to": None, "on": True}),
    ]


def test_a_route_behind_a_flag_is_found_only_where_it_is_on(client, session, account, monkeypatch):
    early = registered(monkeypatch)
    router = APIRouter(dependencies=[Depends(flagged(early))])

    @router.get("/early")
    def preview():
        return {"early": True}

    client.app.include_router(router, prefix="/api")
    hidden = client.get("/api/early")
    assert (hidden.status_code, hidden.json()) == (404, {"detail": NOT_FOUND})
    assert client.get("/api/auth/me").json()["organization"]["flags_on"] == []

    organization = session.get(Organization, account.organization.id)
    choose(session, organization, early, True)
    session.commit()
    assert client.get("/api/early").json() == {"early": True}
    assert client.get("/api/auth/me").json()["organization"]["flags_on"] == [early]


def test_the_worker_reads_a_flag_by_its_organisations_id(session, make_account, monkeypatch):
    early = registered(monkeypatch)
    org_id = make_account("ann@globex.example").organization.id
    assert not flag_on_for(session, org_id, early)
    session.get(Organization, org_id).flags = {early: True}
    session.commit()
    assert flag_on_for(session, org_id, early)


def test_the_rollouts_count_every_organisation(session, make_account, monkeypatch):
    assert rollouts(session) == []
    early = registered(monkeypatch, share=100)
    later = registered(monkeypatch, "later")
    globex = make_account("ann@globex.example").organization
    initech = make_account("bob@initech.example").organization
    make_account("cy@acme.example")
    choose(session, globex, early, False)
    choose(session, initech, later, True)
    session.commit()

    first, second = rollouts(session)
    assert (first.name, first.on, first.organizations) == (early, 2, 3)
    assert first.chosen == [(globex.id, "globex", "Globex", False)]
    assert (second.name, second.on, second.chosen) == (
        later,
        1,
        [(initech.id, "initech", "Initech", True)],
    )


def test_the_command_lists_the_flags_and_turns_one_on_for_an_organisation(
    session, make_account, monkeypatch, capsys
):
    make_account("ann@globex.example")
    main(["flags"])
    assert capsys.readouterr().out == (
        "No feature is behind a flag: FLAGS in app/flags.py names none.\n"
    )

    early = registered(monkeypatch, share=30)
    main(["flags", "globex", early, "on"])
    out = capsys.readouterr().out
    assert out.splitlines()[:2] == [
        "early is on for Globex now.",
        "Globex (globex) has these feature flags:",
    ]
    assert "early  on  turned on for it" in out
    main(["flags", "globex", early, "on"])
    assert "early is on for Globex already, so nothing changed." in capsys.readouterr().out
    main(["flags"])
    assert capsys.readouterr().out == (
        "early, Early access: rolled out to 30 % of organisations, on for 1 of 1 organisation.\n"
        "  Turned on for globex.\n"
    )
    main(["flags", "globex", early, "rollout"])
    out = capsys.readouterr().out
    assert "early follows its rollout for Globex now." in out
    assert "rolled out to 30 % of organisations" in out
    assert session.scalar(select(Organization.flags)) == {}

    with pytest.raises(SystemExit, match="There is no feature flag 'erly'; the flags are early"):
        main(["flags", "globex", "erly", "on"])
    with pytest.raises(SystemExit, match="No organisation has the slug 'acme'"):
        main(["flags", "acme"])
    with pytest.raises(SystemExit):
        main(["flags", "globex", early])
