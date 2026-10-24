"""Connected folders: a folder of a member's Google Drive, Dropbox or OneDrive that a project keeps
in step with (deploying.md, "Connected folders"; behind the flag `connectors`, on the plans
whose `connectors` entitlement is true).

A member who may change a project connects a service from the library's "Connected folders": the
browser goes to the service for the member's consent and comes back with a code, which the app
redeems for a refresh token that it keeps encrypted (app/api/connectors.py); the member then
chooses one of their folders there, and where its files go in the project. The worker syncs it
every SYNC_INTERVAL and at a member's "Sync now" (app/connectors/sync.py). `base.py` is what the
services do alike, each of the others one service.
"""

from app.config import Settings
from app.connectors.base import Provider
from app.connectors.dropbox import Dropbox
from app.connectors.google import GoogleDrive

# Every service's name in the members' words, the ones a deployment has no client for among
# them, for a connector made while it had one.
LABELS = {"google": "Google Drive", "dropbox": "Dropbox", "onedrive": "OneDrive"}


def providers(settings: Settings) -> dict[str, Provider]:
    """The services the deployment has an OAuth client for, by name, in the order offered."""
    found: dict[str, Provider] = {}
    base = settings.connector_base_url
    if settings.google_drive_client_id:
        found["google"] = GoogleDrive(
            settings.google_drive_client_id, settings.google_drive_client_secret, base
        )
    if settings.dropbox_app_key:
        found["dropbox"] = Dropbox(settings.dropbox_app_key, settings.dropbox_app_secret, base)
    return found
