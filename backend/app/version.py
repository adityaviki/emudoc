"""The version a process runs, which `/api/health` and the user menu show and the log names at
start, so that an operator can tell which release answered and a release can tell that it took.

An image carries its version in `EMUDOC_VERSION` (the Dockerfile's `VERSION` build argument):
a release's number for one published on a version tag (`.github/workflows/publish.yml`), else
the full SHA of the commit it was built from, which CI and `deploy/aws/push-image.sh` pass and
the image's tag repeats. A checkout has none: its files may be any commit's, edited or not, so
it says `development` rather than guess.
"""

import os
from collections.abc import Mapping

DEVELOPMENT = "development"


def running_version(environ: Mapping[str, str] = os.environ) -> str:
    return environ.get("EMUDOC_VERSION", "").strip() or DEVELOPMENT


VERSION = running_version()
