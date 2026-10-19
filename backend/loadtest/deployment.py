"""The app as the load test runs it: production's shape on this machine. The API (uvicorn, as
the image's command starts it) and the worker are processes of their own, each serving its
metrics, against a database of their own on the compose server, made afresh, and the stand-in
(frontend/e2e/standin.py) for the models and the embeddings endpoint, so that a run costs
nothing and measures the app's own time. Every setting a figure depends on is set here, so that
a developer's .env changes none of them."""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
from prometheus_client.parser import text_string_to_metric_families

BACKEND = Path(__file__).resolve().parent.parent
STANDIN = BACKEND.parent / "frontend" / "e2e" / "standin.py"
DATABASE = "emudoc_load"
# The compose file's bucket (`docker compose --profile s3 up -d --wait`).
BUCKET = {
    "AWS_ENDPOINT_URL": "http://127.0.0.1:9000",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "emudoc",
    "AWS_SECRET_ACCESS_KEY": "emudoc-secret",
}

# The defaults the figures are of, named so that .env moves none of them; `--set` changes one
# for a run, and the report lists what was changed.
SIZING = {
    "WORKER_CONCURRENCY": "2",
    "ANSWER_CONCURRENCY": "8",
    "DB_POOL_SIZE": "5",
    "DB_MAX_OVERFLOW": "10",
    "DB_POOL_TIMEOUT": "5",
    "RENDER_PROCESSES": "2",
    "PRERENDER_PAGES": "3",
    "MAX_ANSWERS": "20",
    "MAX_UPLOAD_MB": "100",
    "MAX_UPLOAD_FILES": "100",
    "MAX_UPLOAD_REQUEST_MB": "500",
    "PARSE_TIMEOUT": "300",
    "PARSE_MEMORY_MB": "2048",
    "PARSE_MAX_PAGES": "500",
    "PARSE_MAX_ROWS": "100000",
    "RENDER_TIMEOUT": "30",
    "RENDER_MEMORY_MB": "1024",
    "OCR_MAX_PAGES": "100",
}
# What the test is not about, out of its way: the rates a minute per member, which one member
# asking for twenty would meet first. The report says so beside the figures.
LIFTED = {"RATE_UPLOADS": "0", "RATE_QUESTIONS": "0", "RATE_SEARCHES": "0"}


@dataclass
class Ports:
    api: int = 18100
    api_metrics: int = 18101
    worker_metrics: int = 18102
    standin: int = 18103


@dataclass
class Deployment:
    directory: Path
    server: str
    storage: str = "file"
    ports: Ports = field(default_factory=Ports)
    changed: dict[str, str] = field(default_factory=dict)
    processes: dict[str, subprocess.Popen] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.ports.api}"

    @property
    def database_url(self) -> str:
        return f"postgresql+psycopg://{self.server}/{DATABASE}"

    def settings(self) -> dict[str, str]:
        standin = f"http://127.0.0.1:{self.ports.standin}/v1"
        if self.storage == "s3":
            storage = {"STORAGE_URL": f"s3://emudoc/load-{int(time.time())}", **BUCKET}
        else:
            storage = {"STORAGE_URL": f"file://{self.directory / 'files'}"}
        return {
            "ENVIRONMENT": "development",
            "LOG_FORMAT": "json",
            "RUN_WORKER": "false",
            "LOCAL_ACCOUNT": "false",
            "DATABASE_URL": self.database_url,
            "DATABASE_POOLER": "",
            "DATABASE_LISTEN_URL": "",
            "APP_URL": self.base,
            "AGENT_MODEL": "ollama:standin",
            "INDEX_MODEL": "ollama:standin",
            "AGENT_FALLBACK_MODEL": "",
            "INDEX_FALLBACK_MODEL": "",
            "OLLAMA_BASE_URL": standin,
            "EMBEDDING_BASE_URL": standin,
            "EMBEDDING_MODEL": "standin",
            "EMBEDDING_DIM": "768",
            "DISABLED_PROVIDERS": "",
            "DATA_REGION": "",
            "SCANNER_URL": "off",
            "SMTP_URL": "",
            "MODEL_PRICES": "",
            "DAILY_SPEND_CAP": "0",
            "SENTRY_DSN": "",
            **storage,
            **SIZING,
            **LIFTED,
            **self.changed,
        }

    def environment(self, **extra: str) -> dict[str, str]:
        return {**os.environ, **self.settings(), "TMPDIR": str(self._tmp()), **extra}

    def _tmp(self) -> Path:
        path = self.directory / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def start(self, **standin: float | bool) -> None:
        """A fresh database, migrated; then the stand-in, the API and the worker, each waited
        for."""
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.storage == "s3":
            try:
                httpx.get(BUCKET["AWS_ENDPOINT_URL"], timeout=2)
            except httpx.HTTPError:
                sys.exit(
                    "No bucket answers on 127.0.0.1:9000: "
                    "docker compose -f docker-compose.yml --profile s3 up -d --wait"
                )
        # A fresh database numbers its documents from 1 again, and the app refuses a place in
        # the storage that holds a file already (`place_taken`), so the last run's files go.
        shutil.rmtree(self.directory / "files", ignore_errors=True)
        with psycopg.connect(f"postgresql://{self.server}/emudoc", autocommit=True) as db:
            db.execute(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)")
            db.execute(f"CREATE DATABASE {DATABASE}")
        subprocess.run(
            ["uv", "run", "--locked", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            env=self.environment(),
            check=True,
            capture_output=True,
        )
        self.start_standin(**standin)
        self._launch(
            "api",
            [
                "uv", "run", "--locked", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                "--port", str(self.ports.api), "--timeout-graceful-shutdown", "8",
            ],
            METRICS_ADDRESS=f"127.0.0.1:{self.ports.api_metrics}",
        )  # fmt: skip
        self._launch(
            "worker",
            ["uv", "run", "--locked", "python", "-m", "app.worker", "--grace", "8"],
            METRICS_ADDRESS=f"127.0.0.1:{self.ports.worker_metrics}",
        )
        _wait_for(f"{self.base}/api/health", self.processes["api"])
        _wait_for(f"http://127.0.0.1:{self.ports.worker_metrics}/", self.processes["worker"])

    def start_standin(
        self,
        fill: bool = True,
        first_token: float = 0.0,
        words_per_second: float = 0.0,
        index_seconds: float = 0.0,
    ) -> None:
        """Start the stand-in, or start it again with other delays (the app's connections to
        it are made per request)."""
        if old := self.processes.pop("standin", None):
            _end(old)
        command = [
            sys.executable,
            str(STANDIN),
            "--port",
            str(self.ports.standin),
            "--first-token",
            str(first_token),
            "--words-per-second",
            str(words_per_second),
            "--index-seconds",
            str(index_seconds),
        ] + (["--fill"] if fill else [])
        self._launch("standin", command)
        _wait_for(f"http://127.0.0.1:{self.ports.standin}/health", self.processes["standin"])

    def _launch(self, name: str, command: list[str], **extra: str) -> None:
        log = (self.directory / f"{name}.log").open("ab")
        self.processes[name] = subprocess.Popen(
            command,
            cwd=BACKEND,
            env=self.environment(**extra),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def stop(self) -> None:
        for name in ("api", "worker", "standin"):
            if process := self.processes.pop(name, None):
                _end(process)

    def metrics(self, process: str) -> dict[str, list[tuple[dict[str, str], float]]]:
        """The process's metrics now: each sample's name with its labels and value."""
        port = self.ports.api_metrics if process == "api" else self.ports.worker_metrics
        text = httpx.get(f"http://127.0.0.1:{port}/", timeout=10).text
        samples: dict[str, list[tuple[dict[str, str], float]]] = {}
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                samples.setdefault(sample.name, []).append((sample.labels, sample.value))
        return samples

    def query(self, sql: str, *params: object) -> list[tuple]:
        with psycopg.connect(f"postgresql://{self.server}/{DATABASE}") as db:
            return db.execute(sql, params).fetchall()

    def maintenance(self, *arguments: str) -> str:
        done = subprocess.run(
            ["uv", "run", "--locked", "python", "-m", "app.maintenance", *arguments],
            cwd=BACKEND,
            env=self.environment(),
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout

    @contextmanager
    def memory(self) -> Iterator["Peaks"]:
        """Sample the resident memory of the API's and the worker's processes, their children
        included (the parse and render processes), twice a second while the block runs."""
        peaks = Peaks()
        done = threading.Event()

        def sample() -> None:
            while not done.wait(0.5):
                for name in ("api", "worker"):
                    if process := self.processes.get(name):
                        peaks.see(name, _tree_rss(process.pid))

        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            yield peaks
        finally:
            done.set()
            thread.join()


@dataclass
class Peaks:
    """The most memory each process tree held, in megabytes."""

    most: dict[str, float] = field(default_factory=dict)

    def see(self, name: str, megabytes: float) -> None:
        self.most[name] = max(self.most.get(name, 0.0), megabytes)


def _wait_for(url: str, process: subprocess.Popen, seconds: float = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            sys.exit(f"{process.args[0]} … ended with {process.returncode}; see its log.")
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    sys.exit(f"Nothing answered at {url} within {seconds:g} seconds.")


def _end(process: subprocess.Popen) -> None:
    """SIGTERM to the process's group (uv runs the server as its child), then SIGKILL."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _tree_rss(root: int) -> float:
    """The resident memory of `root` and every process below it, in megabytes (Linux's /proc)."""
    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            # The command's name is in brackets and may hold spaces; the parent comes after it.
            parents[int(entry.name)] = int(stat.rsplit(")", 1)[1].split()[1])
    tree, grown = {root}, True
    while grown:
        below = {pid for pid, parent in parents.items() if parent in tree} - tree
        tree |= below
        grown = bool(below)
    total = 0
    for pid in tree:
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1])
    return total / 1024
