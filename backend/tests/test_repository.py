"""The repository's rules on GitHub against its workflows. main takes a commit only through a
pull request (.github/rulesets/main.json, which GitHub holds once applied with `gh api`), whose
checks it requires: every job of CI, which runs on every pull request on GitHub's runners and
holds no secret, so that a fork's pull request runs them all, and the contributor licence
agreement's (CONTRIBUTING.md). The repository's admins may merge a pull request before its
checks end, never push past one. The suite through a pooler, the embeddings and the audits run
once what they check changes, and are not required, since a pull request they skip would wait
for them for good."""

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
    """CI's check runs on a pull request: each job's name with its matrix filled in."""
    checks = set()
    for job in workflow("ci.yml")["jobs"].values():
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
    # The repository's admins alone may pass the rules, and only by merging a pull request: the
    # owner merges their own before its checks end, and nobody pushes to main.
    assert rules["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
    ]
    # Neither deleted nor rewritten, and no merge commits.
    assert {"deletion", "non_fast_forward", "required_linear_history"} <= kinds.keys()
    # The checks hold a change back, not an approval, which the one maintainer cannot give
    # their own pull request.
    assert kinds["pull_request"]["required_approving_review_count"] == 0
    assert set(kinds["pull_request"]["allowed_merge_methods"]) == {"rebase", "squash"}


def test_main_requires_every_check_of_a_pull_request_and_the_cla():
    _, kinds = ruleset()
    required = kinds["required_status_checks"]
    # Checked against main as it is now, not as the branch left it.
    assert required["strict_required_status_checks_policy"] is True
    checks = {
        check["context"]: check.get("integration_id")
        for check in required["required_status_checks"]
    }
    # CI's jobs from GitHub Actions, by their check runs' names with the matrix filled in.
    assert {name for name, app in checks.items() if app == 15368} == checks_of_a_pull_request()
    # CLA Assistant's status, set by its app on each pull request.
    assert checks.keys() - checks_of_a_pull_request() == {"license/cla"}


def test_every_pull_request_runs_the_checks_without_a_secret():
    on = triggers("ci.yml")
    assert on["push"] == {"branches": ["main"]} and "pull_request" in on
    for name, job in workflow("ci.yml")["jobs"].items():
        # Every job, on GitHub's runners: none is kept off a pull request.
        assert "if" not in job, name
        assert job["runs-on"] == "ubuntu-24.04", name
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, name
    assert workflow("ci.yml")["permissions"] == {"contents": "read"}
    workflows = sorted((GITHUB / "workflows").glob("*.yml"))
    for path in workflows:
        text = path.read_text()
        # No workflow runs a fork's code with the repository's token, nor on a machine of ours.
        assert "pull_request_target" not in text and "vars." not in text, path.name
        assert "self-hosted" not in text, path.name
    # The one secret is the token that pushes a release's image, in Publish's last step.
    holding = {path.name for path in workflows if "secrets." in path.read_text()}
    assert holding == {"publish.yml"}
    # The suite through a pooler and the embeddings run on a pull request that changes what they
    # check, as on main; the audits on main alone, and once a week for the news.
    for name in ("pooler.yml", "embeddings.yml"):
        on = triggers(name)
        assert on["push"]["branches"] == ["main"] and on["push"]["paths"], name
        assert on["pull_request"]["paths"] == on["push"]["paths"], name
        assert all("if" not in job for job in workflow(name)["jobs"].values()), name
    audit = triggers("audit.yml")
    assert audit["push"]["paths"] and "pull_request" not in audit
    assert [entry["cron"].split()[-1] for entry in audit["schedule"]] == ["1"]
    # A release's image is published on a version tag alone, and by hand only checked.
    publish = triggers("publish.yml")
    assert set(publish) == {"push", "workflow_dispatch"}
    assert publish["push"] == {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}
    # No job of another workflow shares a name with one of CI's, which would make the
    # ruleset's check ambiguous.
    others = {
        job["name"]
        for path in workflows
        if path.name != "ci.yml"
        for job in workflow(path.name)["jobs"].values()
    }
    assert not others & checks_of_a_pull_request()


def test_a_release_s_image_is_pushed_only_once_both_platforms_ran():
    # Each platform's image runs the compose file (check-image.sh) and the scan passes before
    # the one step that pushes, which a run by hand skips; nothing else holds the token.
    (job,) = workflow("publish.yml")["jobs"].values()
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    names = [step.get("name") for step in job["steps"]]
    assert names.index("Push") == len(names) - 1
    runs = [step.get("run", "") for step in job["steps"][: names.index("Push")]]
    for platform in ("amd64", "arm64"):
        assert f".github/scripts/check-image.sh emudoc:{platform}" in runs
    assert any("--exit-code 1" in run for run in runs)
    push = job["steps"][-1]
    assert push["if"] == "env.PUSH == 'true'"
    assert job["env"]["PUSH"] == (
        "${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}"
    )
    assert all("secrets." not in str(step) for step in job["steps"][:-1])
    # The image's name is the one the compose file runs by default.
    compose = (ROOT_DIR / "deploy" / "compose.yml").read_text()
    assert "ghcr.io/adityaviki/emudoc:${EMUDOC_TAG:-latest}" in compose
    assert "IMAGE=ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/emudoc" in str(job["steps"])


def test_the_published_tree_leaves_the_owner_s_tools_out():
    # What a contributor runs works without the owner's tools.
    dev = (ROOT_DIR / "dev.sh").read_text()
    assert "follow" not in dev and "worktree" not in dev and "systemctl" not in dev
    # The public repository starts from `git archive` of main, which leaves out what the private
    # archive's .gitattributes marks export-ignore, itself among them: the published tree holds
    # neither the tools nor the file.
    attributes = ROOT_DIR / ".gitattributes"
    tools = {"follow.sh", "worktree.sh"}
    if not attributes.exists():
        assert not any((ROOT_DIR / name).exists() for name in tools)
        return
    lines = [line.split() for line in attributes.read_text().splitlines()]
    ignored = {words[0] for words in lines if words[1:] == ["export-ignore"]}
    assert tools | {".gitattributes"} <= ignored
