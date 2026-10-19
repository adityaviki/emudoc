"""The load test: the app on this machine in production's shape, against a stand-in model, with
a member who uploads a library of thousands of documents, searches it, asks twenty questions
at once, and uploads a long scan and large workbooks. It prints what the member waited for at
the 50th and 95th percentiles, and what the app held and did meanwhile (deploying.md, "Load").

    uv --directory backend run python -m loadtest [--documents 3000] [--storage s3] [--out f.json]

It needs the compose database (docker compose up -d --wait db), and the compose bucket for
`--storage s3`. It makes the database emudoc_load afresh and drops nothing else.
"""

import argparse
import asyncio
import json
import os
import platform
import random
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict
from pathlib import Path

from loadtest import corpus
from loadtest.deployment import BACKEND, LIFTED, SIZING, Deployment
from loadtest.member import Answer, Member
from loadtest.report import Report, say

PHASES = ("library", "search", "chats", "paced", "large")
STAGES = ("scan", "parse", "structure", "enrich", "embed")
SETTLED = ("ready", "failed", "quarantined")
# The candidates a search for a page of ten asks the vector ranking for (`hybrid_search`), and
# the `hnsw.ef_search` values they are found at, pgvector's default first.
CANDIDATES = 40
EF_SEARCH = (40, 100, 200, 400)


async def bounded(limit: int, work: Iterable[Callable[[], Awaitable]]) -> list:
    """Run the coroutines `work` makes, at most `limit` at a time, in order of their results."""
    gate = asyncio.Semaphore(limit)

    async def one(make: Callable[[], Awaitable]):
        async with gate:
            return await make()

    return await asyncio.gather(*(one(make) for make in work))


class Run:
    def __init__(self, args: argparse.Namespace, deployment: Deployment):
        self.args = args
        self.deployment = deployment
        self.report = Report()
        self.member = Member(deployment.base)
        self.world = corpus.World(random.Random(7))
        self.library: int | None = None
        self.chats: list[int] = []

    async def wait_settled(
        self, project_id: int, count: int, during: Callable[[], Awaitable] | None = None
    ) -> float:
        """Wait until `count` documents of the project are ready, failed or quarantined,
        running `during` over and over meanwhile; return the seconds it took."""
        began = time.monotonic()
        said = began
        while True:
            ((settled,),) = self.deployment.query(
                "SELECT count(*) FROM documents WHERE project_id = %s AND status = ANY(%s)",
                project_id,
                list(SETTLED),
            )
            if settled >= count:
                return time.monotonic() - began
            if time.monotonic() - said > 30:
                say(f"  … {settled:,} of {count:,} settled after {time.monotonic() - began:.0f} s")
                said = time.monotonic()
            if during:
                await during()
            else:
                await asyncio.sleep(1)

    async def library_phase(self) -> None:
        args = self.args
        say(f"Library: {args.documents:,} files, {args.batch} a request, {args.uploaders} at once")
        paths = corpus.library(self.deployment.directory / "library", args.documents)
        self.library = await self._library("library", "Library", paths, search=True)

    async def paced_phase(self) -> None:
        """A smaller library read by a model that takes its time, which the stand-in at once
        hides: a document's wait for an indexer is the work queued ahead of it."""
        args = self.args
        say(f"Paced: {args.paced_documents} files, {args.index_seconds:g} s an indexing request")
        paths = corpus.library(self.deployment.directory / "library", args.paced_documents)
        self.deployment.start_standin(index_seconds=args.index_seconds)
        try:
            await self._library("paced", f"{args.index_seconds:g} s a request", paths)
        finally:
            self.deployment.start_standin()

    async def _library(self, phase: str, name: str, paths: list[Path], search: bool = False) -> int:
        """Upload `paths` into a new project as the batches and uploaders say, wait until every
        document is settled (searching the project meanwhile when asked), and report it."""
        args, report = self.args, self.report
        project = await self.member.project(name)
        batches = [paths[start : start + args.batch] for start in range(0, len(paths), args.batch)]
        before = self.deployment.metrics("worker")
        began = time.monotonic()
        results = await bounded(
            args.uploaders,
            (lambda batch=batch: self.member.upload(project, batch) for batch in batches),
        )
        uploaded = time.monotonic() - began
        rejected = sum(len(result["rejected"]) for _, result in results)
        report.add(phase, f"Upload, a request of {args.batch} files", (s for s, _ in results))
        searches: list[float] = []
        queries = (self.world.query() for _ in iter(int, 1))

        async def search_meanwhile() -> None:
            url = f"/api/projects/{project}/search"
            searches.append(await self.member.timed_get(url, q=next(queries), limit=10))
            await asyncio.sleep(0.5)

        during = search_meanwhile if search else None
        with self.deployment.memory() as peaks:
            indexed = uploaded + await self.wait_settled(project, len(paths) - rejected, during)
        after = self.deployment.metrics("worker")
        if search:
            report.add(phase, "Search while the library is indexed", searches)
        failed, ready = self._ready(project)
        report.add(phase, "Upload to ready (documents.ready_at)", ready)
        if failed or rejected:
            report.fault(f"{name}: {failed} documents not ready, {rejected} refused at the upload")
        stages = {
            stage: _mean_delta(before, after, "emudoc_stage_seconds", stage=stage)
            for stage in STAGES
        }
        calls = _sum_delta(before, after, "emudoc_model_requests_total", role="index")
        settings = self.deployment.settings()
        report.note(
            f"{name}: {len(paths):,} files uploaded in {uploaded:.0f} s, {rejected} refused, all "
            f"settled {indexed:.0f} s after the first ({len(paths) / indexed * 60:,.0f} a minute "
            f"with WORKER_CONCURRENCY {settings['WORKER_CONCURRENCY']}), {failed} failed; a "
            "stage took on average "
            + ", ".join(f"{stage} {seconds:.2f} s" for stage, seconds in stages.items())
            + f"; {calls / len(paths):.1f} indexing requests a document; most memory: "
            + ", ".join(f"{process} {mb:,.0f} MB" for process, mb in peaks.most.items())
        )
        counts = self.deployment.query(
            "SELECT (SELECT count(*) FROM passages WHERE project_id = %s),"
            " (SELECT count(*) FROM facts WHERE project_id = %s),"
            " pg_size_pretty(pg_database_size(current_database()))",
            project,
            project,
        )[0]
        report.note(
            f"{name}: {counts[0]:,} passages, {counts[1]:,} facts; the database: {counts[2]}"
        )
        return project

    def _ready(self, project_id: int) -> tuple[int, list[float]]:
        """How many of the project's documents are not ready, and the seconds from each ready
        one's upload to its first indexing's end (`emudoc_document_ready_seconds`'s measure)."""
        rows = self.deployment.query(
            "SELECT status, extract(epoch FROM ready_at - created_at)::float FROM documents"
            " WHERE project_id = %s",
            project_id,
        )
        return sum(status != "ready" for status, _ in rows), [s for _, s in rows if s is not None]

    async def search_phase(self) -> None:
        args, report, project = self.args, self.report, self.library
        assert project is not None, "the search phase searches the library phase's project"
        say(f"Search: {args.searches} searches in the library")
        url = f"/api/projects/{project}/search"
        for concurrency in (1, args.searchers):
            queries = [self.world.query() for _ in range(args.searches)]
            times = await bounded(
                concurrency,
                (lambda q=q: self.member.timed_get(url, q=q, limit=10) for q in queries),
            )
            report.add("search", f"Search, {concurrency} at a time", times)
        facts = f"/api/projects/{project}/facts"
        suppliers = [supplier.split()[0] for supplier in self.world.suppliers]
        words = [random.Random(n).choice(suppliers) for n in range(args.searches // 3)]
        # A piece of a name, which only `ilike` finds: no text search matches half a word.
        fragments = [word[1:-1].lower() for word in words]
        for label, terms in (("a whole word", words), ("a piece of a word", fragments)):
            times = await bounded(1, (lambda q=q: self.member.timed_get(facts, q=q) for q in terms))
            report.add("search", f"Facts matching {label} (ilike)", times)
            report.add(
                "search",
                f"The agent's list_documents, {label} (ilike)",
                self._listed(project, terms),
            )
        queries = [self.world.query() for _ in range(args.searches // 3)]
        self._candidates(project, "the library", queries)
        # Another organisation's documents of the same kinds, from a seed of their own: the
        # library's own files would put each of their passages beside its twin in the index.
        say(f"Search: a project of {args.small_documents} documents beside the library")
        paths = corpus.library(self.deployment.directory / "small", args.small_documents, seed=2)
        small = await self._library("small", "A small project", paths)
        url = f"/api/projects/{small}/search"
        times = await bounded(
            1, (lambda q=q: self.member.timed_get(url, q=q, limit=10) for q in queries)
        )
        report.add("search", f"Search in a project of {len(paths)} documents beside it", times)
        self._candidates(small, "the small project", queries, analyze=True)

    def _listed(self, project_id: int, terms: list[str]) -> list[float]:
        """The seconds the agent's list_documents tool takes for each of `terms`, run in this
        process against the load test's database, as an answer's tool call runs it."""
        os.environ.update(self.deployment.settings())
        from app.agent.tools import ListDocumentsInput, ToolContext, list_documents
        from app.db import SessionLocal

        times = []
        for term in terms:
            with SessionLocal() as session:
                began = time.perf_counter()
                list_documents(
                    ToolContext(session, None, project_id), ListDocumentsInput(query=term)
                )  # type: ignore[arg-type]
                times.append(time.perf_counter() - began)
        return times

    def _candidates(
        self, project_id: int, label: str, queries: list[str], analyze: bool = False
    ) -> None:
        """The vector ranking's candidates for each of `queries`, run in this process as
        `hybrid_search` runs it, against the project's nearest passages by exact distance: the
        time, and how many of the 10 and the 40 nearest they hold, at pgvector's `ef_search` and
        above it; then the same through the vector index whatever the planner would choose,
        which is what a project the planner takes it for gets. `analyze` first gives the
        planner the project's share of the table, as autovacuum would in time."""
        os.environ.update(self.deployment.settings())
        from sqlalchemy import text

        from app.config import get_settings
        from app.db import SessionLocal
        from app.embeddings import Embedder
        from app.search import _VECTOR_CTE

        statement = _VECTOR_CTE.format(doc_filter="")
        # An expression the project's btree cannot serve, so the vector index is the only way.
        indexed = statement.replace("p.project_id = :project_id", "p.project_id + 0 = :project_id")
        assert indexed != statement, "the vector ranking no longer filters by project as it did"
        embedder = Embedder(get_settings())
        vectors = [
            "[" + ",".join(f"{value:.7g}" for value in embedder.embed_query(query)) + "]"
            for query in queries
        ]
        if analyze:
            with SessionLocal() as session:
                session.execute(text("ANALYZE passages"))
                session.commit()

        def ranked(sql: str, vector: str, *settings: str) -> tuple[list[float], float, bool]:
            """The candidates' distances from the query, nearest first, the seconds the
            ranking took, and whether its plan scanned the vector index."""
            params = {"project_id": project_id, "qvec": vector, "candidates": CANDIDATES}
            with SessionLocal() as session:
                # Each case planned with its own settings, never with a plan a connection kept
                # from a case before (`hybrid_search` plans each search likewise).
                for setting in ("plan_cache_mode = force_custom_plan", *settings):
                    session.execute(text(f"SET LOCAL {setting}"))
                plan = session.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"), params).scalar()
                began = time.perf_counter()
                ids = session.execute(text(sql), params).scalars().all()
                took = time.perf_counter() - began
                distances = session.scalars(
                    text(
                        "SELECT embedding <=> CAST(:qvec AS vector) FROM passages"
                        " WHERE id = ANY(:ids) ORDER BY 1"
                    ),
                    {"qvec": vector, "ids": list(ids)},
                ).all()
                session.rollback()
            return list(distances), took, "ix_passages_embedding" in json.dumps(plan)

        exact = [ranked(statement, vector, "enable_indexscan = off") for vector in vectors]
        self.report.add(
            "search", f"Vector candidates in {label}, by exact distance", (t for _, t, _ in exact)
        )
        found = []
        iterative = "hnsw.iterative_scan = 'relaxed_order'"
        cases = [(f"ef_search {ef}", statement, (f"hnsw.ef_search = {ef}",)) for ef in EF_SEARCH]
        cases.append(
            (
                f"through the index, ef_search {EF_SEARCH[0]}",
                indexed,
                ("enable_seqscan = off", "enable_bitmapscan = off"),
            )
        )
        for case, sql, extra in cases:
            runs = [ranked(sql, vector, iterative, *extra) for vector in vectors]
            self.report.add(
                "search", f"Vector candidates in {label}, {case}", (t for _, t, _ in runs)
            )
            pairs = [(c, t) for (c, _, _), (t, _, _) in zip(runs, exact, strict=True)]
            # The 10 nearest are what a page of ten draws on; the candidates are all fused.
            ten = [_share_found(c, t, 10) for c, t in pairs]
            every = [_share_found(c, t, CANDIDATES) for c, t in pairs]
            found.append(
                f"{case}: the 10 nearest {sum(ten) / len(ten):.1%} found on average "
                f"({min(ten):.0%} at worst), the {CANDIDATES} nearest "
                f"{sum(every) / len(every):.1%} ({min(every):.0%}), the nearest missed by "
                f"{sum(_share_found(c, t, 1) < 1 for c, t in pairs)}, "
                f"{min(len(c) for c, _ in pairs)} candidates at fewest, the index scanned for "
                f"{sum(used for _, _, used in runs)}"
            )
        ((passages,),) = self.deployment.query(
            "SELECT count(*) FROM passages WHERE project_id = %s", project_id
        )
        ((table,),) = self.deployment.query("SELECT count(*) FROM passages")
        self.report.note(
            f"Vector candidates in {label} ({passages:,} of the table's {table:,} passages, "
            f"{len(vectors)} searches): " + "; ".join(found)
        )

    async def chats_phase(self) -> None:
        args, project = self.args, self.library
        assert project is not None, "the chats phase asks about the library phase's project"
        self.chats = [await self.member.chat(project) for _ in range(args.chats)]
        await self._rounds("chats", f"{args.chats} at once, the stand-in at once", args.rounds)
        self.deployment.start_standin(
            first_token=args.first_token, words_per_second=args.words_per_second
        )
        try:
            await self._rounds(
                "chats",
                f"{args.chats} at once, {args.first_token:g} s to a turn's first token, "
                f"{args.words_per_second:g} words a second",
                args.rounds,
            )
        finally:
            self.deployment.start_standin()

    async def _rounds(self, phase: str, label: str, rounds: int) -> list[Answer]:
        say(f"Chats: {rounds} rounds of {len(self.chats)} questions, {label}")
        before = self.deployment.metrics("worker")
        answers: list[Answer] = []
        for _ in range(rounds):
            answers += await asyncio.gather(
                *(self.member.ask(chat, self.world.question()) for chat in self.chats)
            )
        self._answers(phase, label, answers, before)
        return answers

    def _answers(self, phase: str, label: str, answers: list[Answer], before) -> None:
        report = self.report
        report.add(phase, f"Ask ({label})", (a.asked for a in answers))
        report.add(phase, f"Question to first event ({label})", (a.first_event for a in answers))
        report.add(phase, f"Question to first text ({label})", (a.first_text for a in answers))
        report.add(phase, f"Question to the answer's end ({label})", (a.ended for a in answers))
        statuses = {
            status: sum(a.status == status for a in answers)
            for status in {a.status for a in answers}
        }
        waited = _mean_delta(
            before,
            self.deployment.metrics("worker"),
            "emudoc_job_wait_seconds",
            kind="answer_question",
        )
        drops = sum(a.drops for a in answers)
        report.note(
            f"Answers ({label}): {statuses}, their streams followed again {drops} times; a job "
            f"waited {waited:.2f} s for an answerer on average"
        )
        if set(statuses) != {"done"}:
            report.fault(f"Answers ({label}) did not all end done: {statuses}")

    async def large_phase(self) -> None:
        """The large files one at a time, read by a model that takes `--large-index-seconds` a
        request: a scan's pages are each a request, so its time is the model's."""
        args = self.args
        if args.large_index_seconds:
            self.deployment.start_standin(index_seconds=args.large_index_seconds)
        try:
            await self._large_files()
        finally:
            if args.large_index_seconds:
                self.deployment.start_standin()

    async def _large_files(self) -> None:
        args = self.args
        directory = self.deployment.directory / "large"
        directory.mkdir(exist_ok=True)
        say("Large files: making them (kept for the next run)")
        scan = corpus.scanned_pdf(directory / f"scan-{args.scan_pages}-pages.pdf", args.scan_pages)
        files = [(scan, f"a {args.scan_pages}-page scan")]
        if args.sheet_mb:
            # Within PARSE_MAX_ROWS a sheet, and the same orders in one sheet, past it.
            rows = int(SIZING["PARSE_MAX_ROWS"]) * 9 // 10
            name = f"orders-{args.sheet_mb}mb"
            sheets = corpus.workbook(directory / f"{name}.xlsx", args.sheet_mb, rows)
            one_sheet = corpus.workbook(directory / f"{name}-one-sheet.xlsx", args.sheet_mb, 10**9)
            files += [
                (sheets, f"a {args.sheet_mb} MB workbook, {rows:,} rows a sheet"),
                (one_sheet, f"a {args.sheet_mb} MB workbook in one sheet"),
            ]
        project = await self.member.project("Large files")
        for path, label in files:
            await self._large(project, path, label, during_scan=path == scan)
        await self._pages(project, scan)

    async def _large(self, project: int, path: Path, label: str, during_scan: bool) -> None:
        report = self.report
        say(f"Large files: {label}, {path.stat().st_size / 1e6:.1f} MB")
        before = self.deployment.metrics("worker")
        with self.deployment.memory() as peaks:
            seconds, result = await self.member.upload(project, [path])
            report.add("large", f"Upload {label} ({path.stat().st_size / 1e6:.0f} MB)", [seconds])
            meanwhile: list[Answer] = []
            searches: list[float] = []

            async def answers_meanwhile() -> None:
                # One round of the chats while the document is read, and searches between.
                if self.chats and not meanwhile:
                    meanwhile.extend(
                        await asyncio.gather(
                            *(self.member.ask(c, self.world.question()) for c in self.chats)
                        )
                    )
                elif self.library:
                    searches.append(
                        await self.member.timed_get(
                            f"/api/projects/{self.library}/search", q=self.world.query(), limit=10
                        )
                    )
                await asyncio.sleep(0.5)

            took = await self.wait_settled(
                project, (await self._count(project)), answers_meanwhile if during_scan else None
            )
        document = result["documents"][0] if result["documents"] else None
        if document is None:
            report.note(f"{label}: refused at the upload: {result['rejected']}")
            return
        ((status, error),) = self.deployment.query(
            "SELECT status, error FROM documents WHERE id = %s", document["id"]
        )
        after = self.deployment.metrics("worker")
        spent = {
            stage: _sum_delta(before, after, "emudoc_stage_seconds", stage=stage)
            for stage in STAGES
        }
        calls = _sum_delta(before, after, "emudoc_model_requests_total", role="index", outcome="ok")
        report.note(
            f"{label}: {status} {took:.0f} s after the upload"
            + (f" ({error})" if error else "")
            + "; stages "
            + ", ".join(f"{stage} {seconds:.1f} s" for stage, seconds in spent.items() if seconds)
            + f"; {calls:.0f} indexing requests; most memory: "
            + ", ".join(f"{name} {mb:,.0f} MB" for name, mb in peaks.most.items())
        )
        if meanwhile:
            self._answers("large", "while the scan is read", meanwhile, before)
        if searches:
            report.add("large", "Search while the scan is read", searches)

    async def _count(self, project: int) -> int:
        ((count,),) = self.deployment.query(
            "SELECT count(*) FROM documents WHERE project_id = %s", project
        )
        return count

    async def _pages(self, project: int, scan: Path) -> None:
        ((document, pages),) = self.deployment.query(
            "SELECT id, page_count FROM documents WHERE project_id = %s AND filename = %s",
            project,
            scan.name,
        )
        if not pages:
            return
        say(f"Pages: the scan's {pages} pages, 4 at a time as a member scrolls, twice")
        url = "/api/documents/{}/pages/{}.png"
        for label in ("drawn", "kept"):
            times = await bounded(
                4,
                (
                    lambda n=n: self.member.timed_get(url.format(document, n))
                    for n in range(1, pages + 1)
                ),
            )
            self.report.add("large", f"A page of the scan, {label}", times)


def _share_found(candidates: list[float], truth: list[float], n: int) -> float:
    """The share of the `n` nearest passages by exact distance (`truth`, nearest first) that
    `candidates` found, by their distances: passages at the same distance are as near as each
    other (the stand-in's vectors of words tie often), so a candidate no farther than the n-th
    is one of them."""
    n = min(n, len(truth))
    return min(1.0, sum(d <= truth[n - 1] + 1e-6 for d in candidates) / n) if n else 1.0


def _sum_delta(before, after, name: str, **labels: str) -> float:
    def total(samples) -> float:
        return sum(
            value
            for found, value in samples.get(f"{name}_sum" if name.endswith("seconds") else name, [])
            if labels.items() <= found.items()
        )

    return total(after) - total(before)


def _mean_delta(before, after, name: str, **labels: str) -> float:
    def count(samples) -> float:
        return sum(
            value
            for found, value in samples.get(f"{name}_count", [])
            if labels.items() <= found.items()
        )

    counted = count(after) - count(before)
    return _sum_delta(before, after, name, **labels) / counted if counted else 0.0


def machine() -> dict[str, str]:
    memory = next(
        line
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal")
    )
    model = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        platform.processor(),
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=BACKEND, capture_output=True, text=True
    ).stdout.strip()
    return {
        "cpu": f"{os.cpu_count()} × {model}",
        "memory": f"{int(memory.split()[1]) / 1024**2:.0f} GB",
        "commit": commit,
        "date": time.strftime("%Y-%m-%d", time.gmtime()),
    }


async def main(args: argparse.Namespace) -> None:
    changed = dict(item.split("=", 1) for item in args.set)
    if args.scan_pages > int(SIZING["OCR_MAX_PAGES"]) and "OCR_MAX_PAGES" not in changed:
        changed["OCR_MAX_PAGES"] = str(args.scan_pages)
    deployment = Deployment(args.dir, args.server, args.storage, changed=changed)
    say(f"Starting the API, the worker and the stand-in ({args.dir})")
    deployment.start()
    run = Run(args, deployment)
    try:
        account = await run.member.sign_up(f"load-{int(time.time())}@example.com")
        # The plan's limits are not what the test is about: enterprise has none.
        deployment.maintenance("plan", account["organization"]["slug"], "enterprise")
        for phase in args.phases:
            await getattr(run, f"{phase}_phase")()
    finally:
        await run.member.close()
        deployment.stop()
    report = run.report
    report.note(
        f"Settings changed from the defaults: {changed or 'none'}; lifted: {', '.join(LIFTED)}"
    )
    report.note(f"Storage: {args.storage}; machine: {machine()}")
    print(report.markdown())
    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "faults": report.faults,
                    "machine": machine(),
                    "settings": deployment.settings(),
                    "rows": [asdict(row) for row in report.rows],
                    "notes": report.notes,
                },
                indent=2,
            )
        )
    if report.faults:
        sys.exit(f"The run's own work went wrong, so its figures are not of it: {report.faults}")


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m loadtest", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--documents", type=int, default=3000, help="the library's documents (%(default)s)"
    )
    parser.add_argument("--batch", type=int, default=50, help="files a request (%(default)s)")
    parser.add_argument("--uploaders", type=int, default=2, help="requests at once (%(default)s)")
    parser.add_argument("--searches", type=int, default=300, help="searches a round (%(default)s)")
    parser.add_argument(
        "--searchers",
        type=int,
        default=8,
        help="searches at once in the second round (%(default)s)",
    )
    parser.add_argument(
        "--small-documents",
        type=int,
        default=30,
        help="the project searched beside the library's (%(default)s)",
    )
    parser.add_argument("--chats", type=int, default=20, help="questions at once (%(default)s)")
    parser.add_argument("--rounds", type=int, default=5, help="rounds of them (%(default)s)")
    parser.add_argument(
        "--first-token",
        type=float,
        default=1.0,
        help="the paced rounds' seconds to a turn's first token (%(default)s)",
    )
    parser.add_argument(
        "--words-per-second", type=float, default=40, help="the paced rounds' pace (%(default)s)"
    )
    parser.add_argument(
        "--paced-documents", type=int, default=200, help="the paced library's (%(default)s)"
    )
    parser.add_argument(
        "--index-seconds",
        type=float,
        default=3.0,
        help="the paced library's seconds an indexing request (%(default)s)",
    )
    parser.add_argument(
        "--scan-pages", type=int, default=300, help="the scan's pages (%(default)s)"
    )
    parser.add_argument(
        "--large-index-seconds",
        type=float,
        default=0.0,
        help="the large files' seconds an indexing request, a scan's page each (%(default)s)",
    )
    parser.add_argument(
        "--sheet-mb", type=int, default=50, help="the workbooks' size (%(default)s; 0: none)"
    )
    parser.add_argument("--storage", choices=("file", "s3"), default="file")
    parser.add_argument(
        "--phases",
        type=lambda value: value.split(","),
        default=list(PHASES),
        help=f"some of {','.join(PHASES)}",
    )
    parser.add_argument(
        "--set", action="append", default=[], metavar="NAME=VALUE", help="a setting for the run"
    )
    parser.add_argument(
        "--server", default="emudoc:emudoc@127.0.0.1:5437", help="the compose database server"
    )
    parser.add_argument(
        "--dir", type=Path, default=Path(os.environ.get("TMPDIR", "/tmp")) / "emudoc-load"
    )
    parser.add_argument("--out", type=Path, help="write the figures as JSON here too")
    return parser.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(main(arguments()))
