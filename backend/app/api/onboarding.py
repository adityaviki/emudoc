"""The first steps the projects page lists for the organisation, and the account's choices
about them, the tour and the chat's suggestion to invite the team (app/onboarding.py): the list
is the organisation's, read for the role the member has in it; hiding it or the suggestion and
having seen the tour are the user's, for every organisation. No choice is the organisation's
business, so none is audited."""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.auth import me_out
from app.api.deps import AccountDep, SessionDep
from app.onboarding import first_steps
from app.schemas import MeOut, OnboardingIn, OnboardingOut, OnboardingStepOut

router = APIRouter(tags=["onboarding"])


@router.get("/organization/onboarding", response_model=OnboardingOut)
def onboarding(account: AccountDep, session: SessionDep):
    """The organisation's first steps that the member's role is offered, and which are taken."""
    return OnboardingOut(
        steps=[
            OnboardingStepOut(key=step.key, done=step.done)
            for step in first_steps(session, account)
        ]
    )


@router.patch("/auth/onboarding", response_model=MeOut)
def choose_onboarding(body: OnboardingIn, account: AccountDep, session: SessionDep):
    """Hide the list of first steps or the suggestion to invite the team, or show either
    again, or say the tour was seen or not. A choice already made keeps the moment it was
    made."""
    user = account.user
    now = datetime.now(UTC)
    for chosen, column in (
        (body.checklist_hidden, "checklist_hidden_at"),
        (body.tour_seen, "tour_seen_at"),
        (body.invite_hidden, "invite_hidden_at"),
    ):
        if chosen is not None and chosen != (getattr(user, column) is not None):
            setattr(user, column, now if chosen else None)
    session.commit()
    return me_out(session, account)
