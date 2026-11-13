"""The AWS deployment (deploy/aws) against what the app asks of a deployment: the settings the
OpenTofu module writes, with what the operator adds, are all production asks for, and the env
files the host writes from them (deploy/aws/host/settings) carry them as they are. The module's
own tests are OpenTofu's (deploy/aws/modules/environment/tests); these keep it in step with
Settings.production_problems, which is what a release's migrations refuse to start without."""

import base64
import json
import re
import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from app.config import ROOT_DIR, Settings

AWS = ROOT_DIR / "deploy" / "aws"
MODULE = AWS / "modules" / "environment"

DATABASE_URL = (
    "postgresql+psycopg://emudoc:Abc123@emudoc-staging.abcdefghijkl.eu-central-1.rds.amazonaws.com"
    ":5432/emudoc?sslmode=verify-full&sslrootcert=/etc/emudoc/rds-ca.pem"
)
# What settings.tf writes for an environment, with the values its tests give.
WRITTEN = {
    "APP_URL": "https://staging.emudoc.example.com",
    "STORAGE_URL": "s3://emudoc-staging-documents-123456789012",
    "AWS_DEFAULT_REGION": "eu-central-1",
    "SCANNER_URL": "clamd://clamav:3310",
    "EMBEDDING_BASE_URL": "http://embeddings:8080/v1",
    "CONVERTER_URL": "http://converter:8080",
    "BACKUP_DAYS": "35",
    "SERVER_LOG_DAYS": "30",
    "SENTRY_ENVIRONMENT": "staging",
    "DATABASE_URL": DATABASE_URL,
}
# Written only when the root asks: mail through SES with the queue of its bounces, mail in, and
# an address for the alerts.
WRITTEN_WHEN_ASKED = {
    "MAIL_FROM",
    "SMTP_URL",
    "MAIL_EVENTS_QUEUE",
    "INBOUND_DOMAIN",
    "INBOUND_MAIL_QUEUE",
    "ALERT_EMAIL",
}
# What the module refuses to go without (variables.tf): the models, the operator's choice.
OPERATOR = {
    "AGENT_MODEL": "anthropic:claude-sonnet-5-5",
    "INDEX_MODEL": "anthropic:claude-haiku-5-5",
}
# What the operator puts under the secrets path by hand: the models' keys, the key the
# database's secrets are encrypted with, and SMTP_URL when the module makes no mail.
SECRETS = {
    "SMTP_URL": "smtp://AKIAEXAMPLE:BAx%2Fy%2Bz%3D@email-smtp.eu-central-1.amazonaws.com:587",
    "ANTHROPIC_API_KEY": "sk-ant-example",
    "ENCRYPTION_KEYS": base64.b64encode(bytes(range(32))).decode(),
}
# What the release tells Compose of the environment for Alertmanager (host/release, from the
# host's settings that the module writes into the cloud-config).
ALERTING = {
    "EMUDOC_ENVIRONMENT": "staging",
    "EMUDOC_ALERTS": "arn:aws:sns:eu-central-1:123456789012:emudoc-staging-alerts",
    "EMUDOC_PAGES": "arn:aws:sns:eu-central-1:123456789012:emudoc-staging-pages",
    "EMUDOC_HEARTBEAT": "arn:aws:sns:eu-central-1:123456789012:emudoc-staging-heartbeat",
}
# Written for the app's sake, not production's checks: boto3 signs for the bucket's region, the
# error tracker files the reports by the environment's name, the converter shows Office files as
# they look, which a deployment may do without, and the app holds the bucket's earlier versions
# to how long the backups are kept, which a deployment may leave unsaid. And for the host's: its
# journal keeps the logs as long as SERVER_LOG_DAYS (host/settings), which the app reads nothing
# of.
NOT_CHECKED = {
    "AWS_DEFAULT_REGION",
    "SENTRY_ENVIRONMENT",
    "CONVERTER_URL",
    "BACKUP_DAYS",
    "SERVER_LOG_DAYS",
}


@pytest.fixture(autouse=True)
def no_settings_of_the_suite(monkeypatch):
    """The suite's environment is a development one; nothing of it reaches these settings."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def problems(values: dict[str, str]) -> list[str]:
    # ENVIRONMENT=production is the image's own (Dockerfile).
    fields = {name.lower(): value for name, value in values.items()}
    return Settings(_env_file=None, environment="production", **fields).production_problems({})


def names_in(path, pattern: str) -> set[str]:
    return set(re.findall(pattern, path.read_text()))


def test_the_module_and_the_operator_give_production_all_it_asks():
    assert problems(WRITTEN | OPERATOR | SECRETS) == []


def test_without_any_one_of_them_production_refuses():
    # So the module asks the operator for nothing production does without, and leaves out
    # nothing it needs: each name is one production_problems checks for.
    every = WRITTEN | OPERATOR | SECRETS
    for name in every.keys() - NOT_CHECKED - {"ANTHROPIC_API_KEY"}:
        rest = {key: value for key, value in every.items() if key != name}
        if name == "DATABASE_URL":
            # Every process stops without it, production or not (load_settings).
            with pytest.raises(ValidationError):
                problems(rest)
        else:
            assert problems(rest), name


def test_the_module_writes_what_these_tests_assume():
    written = names_in(MODULE / "settings.tf", r"\b([A-Z][A-Z0-9_]+)\s+=\s")
    assert written == WRITTEN.keys() | WRITTEN_WHEN_ASKED
    required = re.search(r"for name in \[([^\]]*)\]", (MODULE / "variables.tf").read_text())
    assert set(re.findall(r'"([A-Z_]+)"', required[1])) == OPERATOR.keys()


def test_the_shared_settings_list_what_the_operator_must_fill_in():
    shared = names_in(AWS / "shared" / "main.tf", r"\b([A-Z][A-Z0-9_]+)\s+=\s")
    assert OPERATOR.keys() <= shared
    # Nothing the module writes itself, which it would refuse.
    assert not shared & (WRITTEN.keys() | WRITTEN_WHEN_ASKED)


def parameters(values: dict[str, str], secrets: dict[str, str]) -> str:
    entries = [{"Name": f"/emudoc/staging/settings/{n}", "Value": v} for n, v in values.items()]
    entries += [{"Name": f"/emudoc/staging/secrets/{n}", "Value": v} for n, v in secrets.items()]
    entries.append({"Name": "/emudoc/staging/release", "Value": "none"})
    return json.dumps({"Parameters": entries})


def write_env_files(tmp_path, values, secrets) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            AWS / "host" / "settings",
            "--path",
            "/emudoc/staging",
            "--image",
            "registry.example/emudoc:3f2c1ab",
            "--environment",
            ALERTING["EMUDOC_ENVIRONMENT"],
            "--alerts",
            ALERTING["EMUDOC_ALERTS"],
            "--pages",
            ALERTING["EMUDOC_PAGES"],
            "--heartbeat",
            ALERTING["EMUDOC_HEARTBEAT"],
            "--out",
            tmp_path,
            "--home",
            "/etc/emudoc",
        ],
        input=parameters(values, secrets),
        capture_output=True,
        text=True,
        check=False,
    )


def compose_values(text: str) -> dict[str, str]:
    """A Compose env file's double-quoted lines read back as Compose 5.5.1 reads them (checked
    with it by hand): a backslash escapes the next character, $$ is one $, and a $ alone would
    be expanded, which no value written here may hold."""

    def dollar(match: re.Match) -> str:
        assert match[1] == "$", f"Compose would expand ${match[1]}… in {line}"
        return "$"

    values = {}
    for line in text.splitlines():
        name, _, quoted = line.partition("=")
        assert quoted.startswith('"') and quoted.endswith('"'), line
        assert not re.search(r'(?<!\\)(\\\\)*"', quoted[1:-1]), f"a bare quote in {line}"
        value = re.sub(r"\$(.|$)", dollar, quoted[1:-1])
        values[name] = re.sub(r"\\(.)", r"\1", value)
    return values


def test_the_host_writes_each_value_as_it_is(tmp_path):
    # A sender's name may hold anything: quotes, dollars, a backslash, a hash, a trailing space.
    operator = OPERATOR | {"MAIL_FROM": '"O\'Brien & Sons, 1 $HOME St, ${USER} \\ #3" <m@x.io> '}
    run = write_env_files(tmp_path, WRITTEN | operator, SECRETS)
    assert run.returncode == 0, run.stderr
    assert compose_values((tmp_path / "emudoc.env").read_text()) == WRITTEN | operator | SECRETS
    assert (
        compose_values((tmp_path / "compose.env").read_text())
        == {
            "EMUDOC_IMAGE": "registry.example/emudoc:3f2c1ab",
            "EMUDOC_ENV": "/etc/emudoc/emudoc.env",
            "EMUDOC_DOMAIN": "staging.emudoc.example.com",
        }
        | ALERTING
    )
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.iterdir())


def test_other_embedding_weights_reach_compose(tmp_path):
    # deploy/compose.yml downloads and serves the weights these name; the model's name is the
    # app's alone, as llama.cpp's server answers whatever a request names.
    weights = {
        "EMBEDDING_WEIGHTS_URL": "https://huggingface.co/m/resolve/0123abc/model.f16.gguf",
        "EMBEDDING_WEIGHTS_SHA256": "ab" * 32,
    }
    settings = WRITTEN | OPERATOR | weights | {"EMBEDDING_MODEL": "mxbai-embed-large"}
    assert write_env_files(tmp_path, settings, SECRETS).returncode == 0
    compose = compose_values((tmp_path / "compose.env").read_text())
    assert (
        compose
        == {
            "EMUDOC_IMAGE": "registry.example/emudoc:3f2c1ab",
            "EMUDOC_ENV": "/etc/emudoc/emudoc.env",
            "EMUDOC_DOMAIN": "staging.emudoc.example.com",
        }
        | ALERTING
        | weights
    )


def test_the_drill_gets_production_settings_without_the_providers_keys(tmp_path):
    assert write_env_files(tmp_path, WRITTEN | OPERATOR, SECRETS).returncode == 0
    drill_app = dict(
        line.split("=", 1) for line in (tmp_path / "drill-app.env").read_text().splitlines()
    )
    assert "ANTHROPIC_API_KEY" not in drill_app and "DATABASE_URL" not in drill_app
    # By which the dry run checks that the restored secrets can be decrypted.
    assert drill_app["ENCRYPTION_KEYS"] == SECRETS["ENCRYPTION_KEYS"]
    assert drill_app["AWS_CONFIG_FILE"] == "/run/aws/config"
    drill = compose_values((tmp_path / "drill.env").read_text())
    assert drill["RDS_URL"] == DATABASE_URL.replace(
        "emudoc-staging.abcdefghijkl.eu-central-1.rds.amazonaws.com", "HOST"
    )
    assert "--env-file /etc/emudoc/drill-app.env -e DATABASE_URL" in drill["DRILL_APP"]
    assert drill["DRILL_APP"].endswith(" registry.example/emudoc:3f2c1ab")
    # The drill exports the copy's URL; its dry run then starts as production does.
    copy = drill["RDS_URL"].replace(
        "HOST", "emudoc-drill-20261101-050000.example.rds.amazonaws.com"
    )
    settings = {k: v for k, v in drill_app.items() if not k.startswith("AWS_")}
    assert problems(settings | {"DATABASE_URL": copy}) == []


def test_the_journal_keeps_no_line_past_the_period_the_deployment_names(tmp_path):
    # A file a day, deleted at the first rotation after its first line is two days short of
    # SERVER_LOG_DAYS; rotations come a day apart, so no line outlives the period less a day
    # (host/settings).
    assert write_env_files(tmp_path, WRITTEN | OPERATOR, SECRETS).returncode == 0
    journal = (tmp_path / "journald.conf").read_text().splitlines()
    assert [line for line in journal if not line.startswith("#")] == [
        "[Journal]",
        "Storage=persistent",
        "MaxFileSec=1day",
        "MaxRetentionSec=28day",
    ]


@pytest.mark.parametrize("days", ["2", "7.5", "", None])
def test_the_host_refuses_a_period_the_journal_cannot_hold_to(tmp_path, days):
    settings = WRITTEN | OPERATOR | {"SERVER_LOG_DAYS": days}
    run = write_env_files(tmp_path, {k: v for k, v in settings.items() if v is not None}, SECRETS)
    assert run.returncode == 1
    assert "SERVER_LOG_DAYS" in run.stderr and "Traceback" not in run.stderr
    assert not list(tmp_path.iterdir())


def aws_compose() -> dict:
    return yaml.safe_load((AWS / "compose.yml").read_text())


def test_every_container_logs_to_the_journal():
    # The journal holds its lines to SERVER_LOG_DAYS; the daemon's driver of the cloud-config
    # rotates by size alone. A service added to either file is named here too.
    base = yaml.safe_load((AWS.parent / "compose.yml").read_text())["services"]
    services = aws_compose()["services"]
    assert base.keys() <= services.keys()
    for name, service in services.items():
        assert service.get("logging") == {"driver": "journald", "options": {"tag": "{{.Name}}"}}, (
            name
        )


def seconds(duration: str) -> int:
    """A duration as Alertmanager writes them here: minutes or seconds."""
    number, unit = re.fullmatch(r"(\d+)([sm])", duration).groups()
    return int(number) * (60 if unit == "m" else 1)


def test_alertmanager_is_told_every_word_of_its_configuration(tmp_path):
    # The container writes each @WORD@ over with a variable of its environment, which Compose
    # takes from compose.env, which the release writes; a word left over would reach SNS as it
    # stands, and the topic would refuse it.
    service = aws_compose()["services"]["alertmanager"]
    script = service["entrypoint"][-1]
    replaced = dict(re.findall(r"s\|@([A-Z]+)@\|\$\$([A-Z_]+)\|g", script))
    configuration = (AWS / "alertmanager.yml").read_text()
    assert set(re.findall(r"@([A-Z]+)@", configuration)) == replaced.keys()
    for variable in replaced.values():
        assert service["environment"][variable].startswith(f"${{{variable}:?")
    assert write_env_files(tmp_path, WRITTEN | OPERATOR, SECRETS).returncode == 0
    compose = compose_values((tmp_path / "compose.env").read_text())
    assert set(replaced.values()) <= compose.keys()


def test_the_watchdog_reaches_the_heartbeat_before_cloudwatch_misses_it():
    rules = yaml.safe_load((ROOT_DIR / "deploy" / "prometheus" / "alerts.yml").read_text())
    by_name = {rule["alert"]: rule for group in rules["groups"] for rule in group["rules"]}
    assert by_name["EmudocWatchdog"]["expr"] == "vector(1)"
    configuration = yaml.safe_load((AWS / "alertmanager.yml").read_text())
    heartbeat = configuration["route"]["routes"][0]
    assert heartbeat["matchers"] == ['alertname="EmudocWatchdog"']
    assert heartbeat["receiver"] == "heartbeat"
    # A message at least once in every period CloudWatch counts (alerts.tf), however Alertmanager
    # rounds its repeat to the group's interval.
    period = int(re.search(r"heartbeat_period\s+=\s+(\d+)", (MODULE / "alerts.tf").read_text())[1])
    every = seconds(heartbeat["repeat_interval"]) + seconds(heartbeat["group_interval"])
    assert every < period
    # The inhibition names rules that carry the label it matches on.
    (inhibit,) = configuration["inhibit_rules"]
    sources = re.fullmatch(r'alertname=~"(.+)"', inhibit["source_matchers"][0])[1].split("|")
    assert all(by_name[name]["labels"]["process"] for name in sources)


def test_what_is_critical_pages_and_reaches_the_alerts_topic_too():
    # Where each label set goes is amtool's to say (CI's "Alerting rules"): this holds the order
    # it rests on, the page first and going on.
    configuration = yaml.safe_load((AWS / "alertmanager.yml").read_text())
    critical = [
        (route["receiver"], route.get("continue", False))
        for route in configuration["route"]["routes"]
        if route["matchers"] == ['severity="critical"']
    ]
    assert critical == [("pages", True), ("alerts", False)]
    receivers = {receiver["name"]: receiver for receiver in configuration["receivers"]}
    (page,) = receivers["pages"]["sns_configs"]
    assert page["topic_arn"] == "@PAGES@"
    # An SMS carries no subject, so the message says which environment pages.
    assert page["message"].startswith("Emudoc @ENVIRONMENT@{{")


def test_the_image_carries_every_file_the_instance_mounts_from_it():
    # A release runs the compose files from the deploy directory the image carries
    # (host/release); a file .dockerignore leaves out would be an empty directory Docker makes.
    allowed = [
        line[1:].rstrip("/")
        for line in (ROOT_DIR / ".dockerignore").read_text().splitlines()
        if line.startswith("!")
    ]
    mounted = {
        volume.split(":")[0]
        for service in aws_compose()["services"].values()
        for volume in service.get("volumes", [])
        if volume.startswith("./")
    }
    assert mounted
    for source in mounted:
        path = f"deploy/{source.removeprefix('./')}"
        assert (ROOT_DIR / path).exists(), path
        assert any(path == each or path.startswith(f"{each}/") for each in allowed), path


def test_the_host_refuses_a_setting_set_twice(tmp_path):
    run = write_env_files(tmp_path, WRITTEN | OPERATOR, {"APP_URL": "https://elsewhere.example"})
    assert run.returncode == 1
    assert "APP_URL is set twice" in run.stderr
    assert not list(tmp_path.iterdir())


def test_the_host_reports_containers_in_trouble_once_they_last(tmp_path):
    # Compose 5.5.1's answers, as host/watch asks for them; the AWS CLI writes what it was given.
    config = {
        "services": {
            "migrate": {"restart": "no"},
            "api": {"restart": "unless-stopped"},
            "worker": {"restart": "unless-stopped"},
            "clamav": {"restart": "unless-stopped"},
        }
    }
    ok = [
        {"Service": "migrate", "State": "exited", "ExitCode": 0, "Health": ""},
        {"Service": "api", "State": "running", "ExitCode": 0, "Health": "healthy"},
        {"Service": "worker", "State": "running", "ExitCode": 0, "Health": "healthy"},
        {"Service": "clamav", "State": "running", "ExitCode": 0, "Health": "starting"},
    ]
    stuck = [
        {**ok[0], "ExitCode": 1},
        ok[1],
        {**ok[2], "Health": "unhealthy"},
    ]
    (tmp_path / "config.json").write_text(json.dumps(config))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "compose").write_text(
        f'#!/bin/sh\ncase "$1" in config) cat {tmp_path}/config.json ;; '
        f"ps) cat {tmp_path}/ps.json ;; esac\n"
    )
    (fake / "aws").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {tmp_path}/published\n')
    for script in fake.iterdir():
        script.chmod(0o755)
    (tmp_path / "current").mkdir()
    environment = {
        "PATH": f"{fake}:/usr/bin:/bin",
        "EMUDOC_COMPOSE": str(fake / "compose"),
        "EMUDOC_WATCH_STATE": str(tmp_path / "run" / "watch"),
        "EMUDOC_RELEASE_LOCK": str(tmp_path / "run" / "release.lock"),
        "EMUDOC_CURRENT": str(tmp_path / "current"),
        # Nothing listens there, so the instance's id is not waited for.
        "EMUDOC_METADATA": "http://127.0.0.1:9",
        "EMUDOC_ALERTS": ALERTING["EMUDOC_ALERTS"],
        "EMUDOC_PAGES": ALERTING["EMUDOC_PAGES"],
        "EMUDOC_ENVIRONMENT": "staging",
    }

    def look(containers: list[dict]) -> str:
        # One object a line, as Compose writes them since 2.21.
        (tmp_path / "ps.json").write_text("\n".join(json.dumps(each) for each in containers))
        run = subprocess.run(
            [sys.executable, AWS / "host" / "watch"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, run.stderr
        return run.stdout

    def published() -> list[str]:
        sent = tmp_path / "published"
        return sent.read_text().split("sns\n")[1:] if sent.exists() else []

    assert look(ok) == ""
    # Seen once: a release replacing a container, or one restarting, is no trouble yet.
    assert look(stuck) == (
        "In trouble:\n  clamav: has no container\n  migrate: exited with status 1\n"
        "  worker: unhealthy\n"
    )
    assert published() == []
    look(stuck)
    # The whole of it to the alerts topic; a page short enough for an SMS, which shows no subject.
    message, page = published()
    assert f"--topic-arn\n{ALERTING['EMUDOC_ALERTS']}\n" in message
    assert "--subject\nEmudoc staging: clamav, migrate, worker in trouble\n" in message
    assert "  worker: unhealthy\n" in message
    assert f"--topic-arn\n{ALERTING['EMUDOC_PAGES']}\n" in page
    assert page.endswith(
        "--message\nEmudoc staging, for five minutes and more: clamav: has no container; "
        "migrate: exited with status 1; worker: unhealthy. The alerts topic says where to look.\n"
    )
    look(stuck)
    assert len(published()) == 2
    look(ok)
    message, page = published()[-2:]
    assert "--subject\nEmudoc staging: the containers are well again\n" in message
    assert f"--topic-arn\n{ALERTING['EMUDOC_PAGES']}\n" in page
    assert page.endswith("--message\nEmudoc staging: every container runs and is healthy again.\n")
