"""What the deployment can do just now, as the versioned API tells a program (app/api/v1/): in the
words its members read on the page (app/status.py), whatever the deployment, and what one upload
may carry, which a program sends its files within."""

from fastapi import APIRouter

from app.api.deps import AccountDep, ServicesDep, SessionDep
from app.api.v1.schemas import Service, Status, UploadLimits
from app.config import get_settings
from app.jobs import work_running
from app.status import KEYWORDS_ONLY, MODELS_UNAVAILABLE, WORK_STOPPED, upload_limits

router = APIRouter(tags=["status"])


@router.get("/status", response_model=Status)
def status(services: ServicesDep, account: AccountDep, session: SessionDep):
    """Whether questions can be answered, search finds passages by meaning and uploads are
    indexed just now, and what one upload may carry."""
    models = services.llm.problem() is None
    search = services.embedder.check() is None
    workers = work_running(session).running
    return Status(
        models=Service(available=models, error=None if models else MODELS_UNAVAILABLE),
        search=Service(available=search, error=None if search else KEYWORDS_ONLY),
        workers=Service(available=workers, error=None if workers else WORK_STOPPED),
        uploads=UploadLimits.model_validate(upload_limits(get_settings()).model_dump()),
    )
