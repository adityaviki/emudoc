"""What a deployment can do just now, as its members are told it: the upload limits a request is
held to, and the sentences for the services that are out. The page reads them from `GET
/api/status` (app/main.py) and a program from `GET /api/v1/status` (app/api/v1/), the same words
for both.

Why a service is out is the operator's, from the start's log, the metrics and the worker's watch
(app/watch.py), which read the same `LLM.problem()` and `Embedder.check()`: these name no model,
address or setting.
"""

from app.config import Settings
from app.parsing import SUPPORTED_EXTENSIONS
from app.schemas import UploadLimitsOut

# What members cannot do for now when the models or the embeddings endpoint cannot be used.
MODELS_UNAVAILABLE = (
    "The AI model is unavailable just now, so questions cannot be asked and documents get no "
    "summaries or facts. Try again later."
)
KEYWORDS_ONLY = (
    "Search matches keywords only just now, so a passage that says the same in other words may "
    "be missed. Try again later."
)
# And when no worker takes the queue's work (app/jobs.py): the operator learns why from the
# processes, `maintenance jobs` and the alerts.
WORK_STOPPED = (
    "Emudoc's background work is not running just now, so uploaded documents wait to be indexed "
    "and questions cannot be asked. Try again later."
)


def upload_limits(settings: Settings) -> UploadLimitsOut:
    """What one upload request may carry, which the page and the upload client send within."""
    megabyte = 1024 * 1024
    return UploadLimitsOut(
        files=settings.max_upload_files,
        request_bytes=settings.max_upload_request_mb * megabyte,
        file_bytes=settings.max_upload_mb * megabyte,
        extensions=sorted(SUPPORTED_EXTENSIONS),
    )
