# Contributing to Emudoc

Thank you for wanting to make Emudoc better. Bug reports, ideas and pull requests are welcome.
For a large change, open an issue first, so that we can agree on its shape before you spend time
on it.

- **A bug or an idea**: open an issue; its form asks for what helps. Search the open ones first.
- **A security problem**: never in an issue. Report it privately, as [`SECURITY.md`](SECURITY.md)
  says.
- **How we behave**: everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Where things are**: [`docs/architecture.md`](docs/architecture.md) maps the code, and each
  module's docstring says what it holds and why.

## The contributor licence agreement

On your first pull request, CLA Assistant asks you to sign the project's contributor licence
agreement ([`CLA.md`](CLA.md)), in a click from the pull request. It is signed once and covers
your later contributions. A pull request cannot be merged until it is signed.

Why: Emudoc is licensed under the AGPL-3.0, and its owner holds the copyright in all of it. The
agreement keeps it that way for what others write: you keep the copyright in your contribution
and give the owner a licence to it, including the right to license it under other terms. That
lets the owner keep offering Emudoc under the AGPL, change how it is licensed later, or run it
as a hosted service with private changes, without having to find and ask every contributor. It
follows the Apache Software Foundation's individual agreement, which many open-source projects
use.

## Setting up

You need Docker, [uv](https://docs.astral.sh/uv/) and [pnpm](https://pnpm.io) with Node 24.

```bash
docker compose up -d --wait            # the development database, on 127.0.0.1:5437
cp .env.example .env                   # the settings, with a model key of your own
./dev.sh                               # the backend on :8000 and the frontend on :5173
git config core.hooksPath .githooks    # once: gitleaks on what you stage (needs Docker)
```

[`docs/development.md`](docs/development.md) says what `dev.sh` does and how the tests work,
and `deploying.md`'s "Every setting" what each setting means.

## Checks

Every pull request runs the checks on GitHub's runners (`.github/workflows/ci.yml`), and they
must pass before it is merged. They run the commands below, so a red check is reproduced on your
checkout:

```bash
uv --directory backend run ruff check . ../frontend/e2e
uv --directory backend run ruff format --check . ../frontend/e2e
uv --directory backend run pytest -q -n auto      # the suite, against the development database
pnpm --dir frontend lint
pnpm --dir frontend test                          # the unit tests
pnpm --dir frontend build && pnpm --dir frontend size
pnpm --dir frontend e2e                           # the browser test, after the build
```

CI also runs the load test at a small size, builds the image and runs it as a team does
(`.github/scripts/check-image.sh`), looks for keys in every commit, and checks the shell scripts,
the workflows, the alerting rules and the Dockerfile. A change to how the app talks to the
database runs the suite through pgbouncer as well (`pooler.yml`), and a change to the embeddings
server checks its vectors (`embeddings.yml`). No job holds a secret, so a pull request from a
fork runs every one.

A change to the database's models comes with its migration (`uv --directory backend run alembic
revision --autogenerate -m "…"`, then `alembic check`); a change to `/api/v1` with its published
contract written again (`uv --directory backend run python -m app.api.v1.contract`); a change to
how a file is read with the parse corpus's snapshots written again (`uv --directory backend run
python -m tests.corpus`), whose diff you read. The tests of the models themselves need real
keys, so CI does not run them: `docs/development.md` says when to.

## Docs

A change says what it changes where people read it, in the same pull request:

- `README.md`, for what Emudoc is and does;
- `deploying.md`, the self-hosting guide, for what an admin sets, runs or watches; a new setting
  also gets its line in `.env.example` and its row in the guide's "Every setting", which
  `backend/tests/test_config.py` holds together;
- the help pages (`backend/app/help/`), for what members see and do;
- `docs/architecture.md`, when the code's shape changes.

## Commits

A commit's subject is one imperative sentence in sentence case that says what changes for
whoever uses the app, without a prefix: "Show thumbnails of PDFs and images in the library". A
change that users or the people who run Emudoc would notice adds its line to
[`CHANGELOG.md`](CHANGELOG.md), under Unreleased and Users or Admins, in the same commit; the
file's introduction says its shape, which the suite holds it to. Pull requests are merged by rebase, so keep
each commit whole: its tests pass on their own.
