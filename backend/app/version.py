"""The version a process runs, which `/api/health` and the user menu show and the log names at
start, so that an operator can tell which release answered and a release can tell that it took.

An image carries its version in `EMUDOC_VERSION` (the Dockerfile's `VERSION` build argument):
a release's number for one published on a version tag (`.github/workflows/publish.yml`), else
the full SHA of the commit it was built from, which CI passes and the image's tag repeats. A
checkout has none: its files may be any commit's, edited or not, so it says `development` rather
than guess.

The AGPL's section 13 has the app offer whoever uses it the source of the version that runs:
the help menu links to `source_url`, the public repository at the release's tag or the commit,
unless SOURCE_URL names a team's own copy (app/config.py).
"""

import os
import re
from collections.abc import Mapping

DEVELOPMENT = "development"
# The public repository, whose tag of each release (v0.1.0) holds that release's source.
REPOSITORY = "https://github.com/adityaviki/emudoc"


def running_version(environ: Mapping[str, str] = os.environ) -> str:
    return environ.get("EMUDOC_VERSION", "").strip() or DEVELOPMENT


VERSION = running_version()


def source_url(configured: str = "", version: str = VERSION) -> str:
    """Where the source of the running version is: SOURCE_URL where it names one, else the
    public repository at the tag of a release's number or at a commit's full SHA, else (a
    checkout, or an image built without a version) the repository itself."""
    if configured:
        return configured
    if re.fullmatch(r"\d+\.\d+\.\d+", version):
        return f"{REPOSITORY}/tree/v{version}"
    if re.fullmatch(r"[0-9a-f]{40}", version):
        return f"{REPOSITORY}/tree/{version}"
    return REPOSITORY
