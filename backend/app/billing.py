"""How an organisation's plan changes: the one function that puts it on a plan, with the seats it
bought and the figures agreed with it, and records in its audit log what changed. The operator's
`plan` command calls it (app/maintenance.py), and billing's webhooks will.

What an organisation may keep and use is read from those three by `entitlements_of`
(app/entitlements.py). Seats are bought on a plan sold by the seat (`BY_THE_SEAT`) and replace
the plan's members; agreed figures replace any of the plan's, which is how an Enterprise
organisation gets its terms. Both belong to the plan they were set on: a move to another plan
clears them unless they are given again, so that an agreement does not outlive its plan. Fewer
seats or lower figures than the organisation uses leave it over its limits without removing
anything: it keeps its members, projects and documents, and the checks refuse what would add
to them.
"""

from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.audit import audit_event
from app.entitlements import BY_THE_SEAT, FIELDS, MODEL_TIERS, PLANS
from app.models import Organization

# The entitlements that are a yes or a no; `model_tier` is one of MODEL_TIERS, `priority` a
# weight of at least 1, and every other one a limit, a count of at least 0 or None for none.
SWITCHES = ("api_access", "connectors")
# Stands for a figure not agreed, which a figure agreed to be no limit (None) is not.
_NONE = object()


def change_plan(
    session: Session,
    organization: Organization,
    plan: str,
    *,
    seats: int | None = None,
    overrides: Mapping[str, object] | None = None,
) -> None:
    """Put the organisation on `plan` with `seats` and the `overrides` agreed with it, the three
    as a whole (None and nothing: the plan's own), and write an audit row for each part that
    changed, one per agreed figure. Raises RuntimeError with the reason for a plan, seats or a
    figure that cannot be. The caller commits."""
    overrides = dict(overrides or {})
    if problem := plan_problem(plan, seats, overrides):
        raise RuntimeError(problem)
    was_plan, was_seats, was_overrides = (
        organization.plan,
        organization.seats,
        dict(organization.overrides or {}),
    )
    organization.plan, organization.seats, organization.overrides = plan, seats, overrides

    def changed(action: str, detail: dict[str, object]) -> None:
        audit_event(session, action, org_id=organization.id, detail=detail)

    if plan != was_plan:
        changed("plan_changed", {"from": was_plan, "to": plan})
    if seats != was_seats:
        changed("seats_changed", {"from": was_seats, "to": seats})
    for name in FIELDS:
        if was_overrides.get(name, _NONE) == overrides.get(name, _NONE):
            continue
        # A figure no longer agreed is the plan's again: `agreed` tells that from one agreed
        # to be no limit, both of which are null.
        changed(
            "entitlements_changed",
            {
                "entitlement": name,
                "from": was_overrides.get(name),
                "to": overrides.get(name),
                "agreed": name in overrides,
            },
        )


def plan_problem(plan: str, seats: int | None, overrides: Mapping[str, object]) -> str | None:
    """Why the organisation cannot be put on `plan` with these seats and figures, or None."""
    if plan not in PLANS:
        return f"There is no {plan!r} plan; the plans are {', '.join(PLANS)}."
    label = plan.capitalize()
    if seats is not None:
        if plan not in BY_THE_SEAT:
            sold = ", ".join(name.capitalize() for name in sorted(BY_THE_SEAT))
            return f"The {label} plan is not sold by the seat; only {sold} is."
        if type(seats) is not int or seats < 1:
            return f"Seats are a whole number of one or more, not {seats!r}."
    for name, value in overrides.items():
        if name not in FIELDS:
            return f"{name!r} is no entitlement; they are {', '.join(FIELDS)}."
        if name == "members" and plan in BY_THE_SEAT:
            return f"The {label} plan's members are its seats; set the seats instead."
        if problem := _figure_problem(name, value):
            return problem
    return None


def _figure_problem(name: str, value: object) -> str | None:
    if name in SWITCHES:
        return None if type(value) is bool else f"{name} is yes or no, not {value!r}."
    if name == "model_tier":
        tiers = ", ".join(MODEL_TIERS)
        return None if value in MODEL_TIERS else f"{name} is one of {tiers}, not {value!r}."
    if name == "priority":
        whole = type(value) is int and value >= 1
        return None if whole else f"{name} is a weight of 1 or more, not {value!r}."
    if value is None or type(value) is int and value >= 0:
        return None
    return f"{name} is a whole number of 0 or more, or none for no limit, not {value!r}."
