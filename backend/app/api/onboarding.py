"""The first steps the projects page lists for the organisation, and the account's choices
about them and the tour (app/onboarding.py): the list is the organisation's, read for the role
the member has in it; hiding it and having seen the tour are the user's, for every
organisation. Neither choice is the organisation's business, so neither is audited."""

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
    """Hide the list of first steps or show it again, or say the tour was seen or not. A choice
    already made keeps the moment it was made."""
    user = account.user
    now = datetime.now(UTC)
    if body.checklist_hidden is not None and body.checklist_hidden != (
        user.checklist_hidden_at is not None
    ):
        user.checklist_hidden_at = now if body.checklist_hidden else None
    if body.tour_seen is not None and body.tour_seen != (user.tour_seen_at is not None):
        user.tour_seen_at = now if body.tour_seen else None
    session.commit()
    return me_out(session, account)
