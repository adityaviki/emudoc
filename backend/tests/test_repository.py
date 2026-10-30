"""The repository's rules on GitHub against its workflows. main takes a commit only through a
pull request whose checks passed (.github/rulesets/main.json, which GitHub holds once applied
with `gh api`, deploying.md "The repository's rules"), and the checks it waits for are the jobs
CI runs on a pull request, by the names GitHub gives their check runs: a required check that no
job reports holds every pull request back for good, and a job left out of the list holds none.
A production release is a job of the GitHub environment production, whose reviewer approves it
and whose jobs alone production's release role trusts (deploy/aws/production)."""

import json
import re
from itertools import product

import yaml

from app.config import ROOT_DIR

GITHUB = ROOT_DIR / ".github"
RULESET = GITHUB / "rulesets" / "main.json"
# The app the check runs of GitHub Actions come from, so that a commit status of the same name,
# which anyone who may push can report, satisfies nothing.
GITHUB_ACTIONS = 15368
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
    """CI's check runs on a pull request: each job's name with its matrix filled in, but those its
    condition keeps to a push to main."""
    checks = set()
    for job in workflow("ci.yml")["jobs"].values():
        if "github.event_name == 'push'" in job.get("if", ""):
            continue
        for values in combinations(job.get("strategy", {}).get("matrix", {})):
            checks.add(named(job["name"], values))
    return checks


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


def test_main_waits_for_every_check_ci_runs_on_a_pull_request():
    _, kinds = ruleset()
    checks = kinds["required_status_checks"]
    required = [check["context"] for check in checks["required_status_checks"]]

    assert sorted(required) == sorted(checks_of_a_pull_request())
    assert "Staging" not in required
    assert {check["integration_id"] for check in checks["required_status_checks"]} == {
        GITHUB_ACTIONS
    }
    # Checked against main as it is, not as it was when the branch left it.
    assert checks["strict_required_status_checks_policy"]
    # No job of another workflow shares a required check's name, which would make it ambiguous:
    # Audit's fail on news rather than on the change, and run on a pull request only when it
    # changes a manifest; Pooler's only when it changes what a pooler sees.
    others = {
        job["name"]
        for name in ("audit.yml", "models.yml", "pooler.yml", "release.yml")
        for job in workflow(name)["jobs"].values()
    }
    assert not others & set(required)


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
