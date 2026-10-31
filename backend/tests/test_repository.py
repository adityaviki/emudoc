"""The repository's rules on GitHub against its workflows. main takes a commit only through a
pull request (.github/rulesets/main.json, which GitHub holds once applied with `gh api`,
deploying.md "The repository's rules"), and while the app is in development (the owner's,
2026-10-31) each check runs once for a change: the backend's suite and the frontend's checks on
the developer's machine before the merge, so CI runs them only by hand and on Dependabot's pull
requests; the heavy jobs once the change is on main; OpenTofu, the embeddings, the suite through
a pooler and the audits once what they check changes there. The developer's own pull request
runs nothing, and the ruleset requires no check, which a check that never reports would hold
back for good. A production release is a job of the GitHub environment production, whose
reviewer approves it and whose jobs alone production's release role trusts
(deploy/aws/production)."""

import json
import re
from itertools import product

import yaml

from app.config import ROOT_DIR

GITHUB = ROOT_DIR / ".github"
RULESET = GITHUB / "rulesets" / "main.json"
MATRIX = re.compile(r"\$\{\{\s*matrix\.(\w+)\s*\}\}")


def workflow(name: str) -> dict:
    return yaml.safe_load((GITHUB / "workflows" / name).read_text())


def named(name: str, values: dict) -> str:
    return MATRIX.sub(lambda found: str(values[found.group(1)]), name)


def combinations(matrix: dict) -> list[dict]:
    """A matrix's jobs as GitHub makes them: each value of every list with each of the others',
    every `include` entry added to the combinations whose own values it agrees with, or made a
    combination of its own when it agrees with none."""
    keys = [key for key in matrix if key != "include"]
    made = [dict(zip(keys, values, strict=True)) for values in product(*map(matrix.get, keys))]
    for extra in matrix.get("include", []):
        shared = extra.keys() & keys
        agreeing = [one for one in made if all(one[key] == extra[key] for key in shared)]
        for one in agreeing:
            one.update(extra)
        if not agreeing:
            made.append(dict(extra))
    return made


def checks_of_a_pull_request() -> set[str]:
    """CI's check runs on the developer's pull request: each job's name with its matrix filled in,
    but those its condition keeps off it (to main, to a run by hand, to Dependabot's)."""
    checks = set()
    for job in workflow("ci.yml")["jobs"].values():
        condition = job.get("if", "")
        if any(
            kept in condition
            for kept in (
                "github.event_name == 'push'",
                "github.event_name != 'pull_request'",
                "github.event_name == 'workflow_dispatch'",
            )
        ):
            continue
        for values in combinations(job.get("strategy", {}).get("matrix", {})):
            checks.add(named(job["name"], values))
    return checks


def triggers(name: str) -> dict:
    """A workflow's `on`, which YAML reads as the key True."""
    loaded = workflow(name)
    return loaded.get("on", loaded.get(True))


def ruleset() -> tuple[dict, dict]:
    rules = json.loads(RULESET.read_text())
    return rules, {rule["type"]: rule.get("parameters", {}) for rule in rules["rules"]}


def test_main_takes_a_commit_only_through_a_pull_request():
    rules, kinds = ruleset()

    assert rules["target"] == "branch" and rules["enforcement"] == "active"
    assert rules["conditions"]["ref_name"] == {"include": ["refs/heads/main"], "exclude": []}
    # Nobody pushes past it, the organisation's owners included.
    assert rules["bypass_actors"] == []
    # Neither deleted nor rewritten, and no merge commits.
    assert {"deletion", "non_fast_forward", "required_linear_history"} <= kinds.keys()
    # The one member may not approve their own pull request: the checks hold a change back.
    assert kinds["pull_request"]["required_approving_review_count"] == 0
    assert set(kinds["pull_request"]["allowed_merge_methods"]) == {"rebase", "squash"}


def test_main_requires_no_check_while_the_developers_pull_requests_run_none():
    _, kinds = ruleset()
    assert "required_status_checks" not in kinds
    assert checks_of_a_pull_request() == set()


def test_each_check_runs_once_for_a_change():
    jobs = workflow("ci.yml")["jobs"]
    kept_to = {name: job.get("if", "") for name, job in jobs.items() if name != "staging"}
    # Checked on the developer's machine before the merge: here by hand and for Dependabot.
    here = {name for name, condition in kept_to.items() if "'workflow_dispatch'" in condition}
    assert here == {"backend", "frontend"}
    # The heavy jobs, once the change is on main, and for Dependabot; staging waits for them.
    heavy = {name for name, condition in kept_to.items() if "!= 'pull_request'" in condition}
    assert heavy == {"e2e", "loadtest", "image", "secrets", "scripts"}
    assert here | heavy == set(kept_to)
    assert all("dependabot[bot]" in kept_to[name] for name in here | heavy)
    assert set(jobs["staging"]["needs"]) == heavy
    # The rest run once a change to what they check is on main, never on the developer's pull
    # request; the embeddings on Dependabot's too, which update llama.cpp's server.
    for name in ("pooler.yml", "audit.yml", "opentofu.yml", "embeddings.yml"):
        on = triggers(name)
        assert on["push"]["branches"] == ["main"] and on["push"]["paths"], name
    assert all("pull_request" not in triggers(name) for name in ("pooler.yml", "audit.yml"))
    assert "pull_request" not in triggers("opentofu.yml")
    # The one workflow that holds a key asks the models every morning and once a change to what
    # it asks with is on main, never on a pull request, whose code could read the key.
    models = triggers("models.yml")
    assert models["push"]["branches"] == ["main"] and models["push"]["paths"]
    assert "pull_request" not in models and models["schedule"]
    assert all("refs/heads/main" in job["if"] for job in workflow("models.yml")["jobs"].values())
    (embeddings,) = workflow("embeddings.yml")["jobs"].values()
    assert "dependabot[bot]" in embeddings["if"]
    # Pooler runs on no schedule, the same tree being checked once; Audit once a week, for news.
    assert "schedule" not in triggers("pooler.yml")
    assert [entry["cron"].split()[-1] for entry in triggers("audit.yml")["schedule"]] == ["1"]
    # No job of another workflow shares a name with one of CI's, which would make it ambiguous.
    others = {
        job["name"]
        for name in ("audit.yml", "models.yml", "pooler.yml", "release.yml")
        for job in workflow(name)["jobs"].values()
    }
    assert not others & {job["name"] for job in jobs.values()}


def test_a_production_release_is_a_job_of_the_environment_its_role_trusts():
    release = workflow("release.yml")["jobs"]["release"]
    aws = ROOT_DIR / "deploy" / "aws"

    assert (
        release["environment"] == "${{ inputs.environment == 'production' && 'production' || '' }}"
    )
    assert re.search(
        r'^\s*github_environment\s*=\s*"production"$',
        (aws / "production" / "main.tf").read_text(),
        re.MULTILINE,
    )
    # Staging's role trusts main, since CI's Staging job, which releases every commit of main,
    # belongs to no environment.
    assert "github_environment" not in (aws / "staging" / "main.tf").read_text()
    assert "environment" not in workflow("ci.yml")["jobs"]["staging"]
