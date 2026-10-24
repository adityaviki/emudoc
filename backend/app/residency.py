"""Where the data goes, region by region, and what holds a deployment to the region it promises.

DATA_REGION (app/config.py) promises the organisations that upload that their documents, and
everything read from them, are kept and processed in one region. The app keeps the promise in
three ways. What it can choose itself, it chooses on every request: Claude's US-only inference and
OpenRouter's regional address (app/llm.py). What it can read, it checks at start
(`residency_problems`): the Vertex AI location, a Bedrock region or inference profile, OpenAI's
European address, the bucket's region and the embeddings endpoint's host. In production a piece
that is outside the region, or whose place the settings cannot show, stops the process
(`check_residency`); in development it is a warning. And what neither the settings nor a
provider can show, where the servers and the database run, is the operator's word: setting
DATA_REGION says they are in the region.

The places below are where the providers stood in October 2026 (deploying.md, "Keeping the data
in one region"). A location this module does not know is in no region, so the check refuses
rather than guesses; adding one is adding it here.

Each organisation records the region its data is kept in (`organizations.region`): as it is
created, and at the first start that names a region for one created before. A deployment whose
region was changed under it, or that was pointed at another region's database, finds
organisations promised another region and refuses to serve them, since moving an organisation's
data is a migration and not a setting.

The subprocessor list (app/subprocessors.py) publishes each `Place` beside the company. Both leave
out what runs on the deployment's own network (`on_own_network` in app/network.py), which is the
deployment itself.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import func, select, update

from app.config import REGIONS, Settings, listing, model_provider
from app.db import SessionLocal
from app.llm import inference_geo, openrouter_url
from app.models import Organization
from app.network import MODEL_BASE_URLS, host_of, on_own_network
from app.storage import Storage

log = logging.getLogger(__name__)

# The region of a place that may be anywhere: a global endpoint, or a provider that names none.
ANYWHERE = "anywhere"
# Google Cloud's locations in the European Union, Vertex AI's `eu` multi-region among them.
# London (europe-west2) and Zurich (europe-west6) are in Europe but not in the Union.
GOOGLE_CLOUD_EU = frozenset(
    {
        "eu",
        "europe-central2",
        "europe-north1",
        "europe-north2",
        "europe-southwest1",
        "europe-west1",
        "europe-west3",
        "europe-west4",
        "europe-west8",
        "europe-west9",
        "europe-west10",
        "europe-west12",
    }
)
# AWS's regions in the European Union; London (eu-west-2) and Zurich (eu-central-2) are not.
AWS_EU = frozenset(
    {"eu-central-1", "eu-north-1", "eu-south-1", "eu-south-2", "eu-west-1", "eu-west-3"}
)
# Hetzner's object storage: Falkenstein and Nuremberg in Germany, and Helsinki.
HETZNER_EU = frozenset({"fsn1", "nbg1", "hel1"})
# Bedrock's cross-region inference profiles, by the prefix of a model id, and the region each
# keeps its requests in: None for a geography outside both, ANYWHERE for the world.
BEDROCK_PROFILES = {
    "eu": "eu",
    "us": "us",
    "us-gov": "us",
    "apac": None,
    "au": None,
    "ca": None,
    "in": None,
    "jp": None,
    "global": ANYWHERE,
}
# The address of OpenAI's projects with European data residency.
OPENAI_EU = "eu.api.openai.com"
# A location in each region, for the sentences that ask for one.
GOOGLE_EXAMPLES = {"eu": "europe-west4", "us": "us-central1"}
AWS_EXAMPLES = {"eu": "eu-central-1", "us": "us-east-1"}
# The models the app can keep in each region, for the sentence that refuses one it cannot.
CHOICES = {
    "eu": (
        "Vertex AI (google-cloud:) or Bedrock (bedrock:eu.<model>) in the European Union, "
        "OpenAI with European data residency, or OpenRouter (openrouter:), which the app sends "
        "to its EU address"
    ),
    "us": (
        "Vertex AI (google-cloud:) or Bedrock (bedrock:us.<model>) in the United States, Claude "
        "(anthropic:), whose inference the app keeps in the US, or OpenRouter (openrouter:), "
        "which the app sends to its US address"
    ),
}


@dataclass(frozen=True)
class Place:
    """Where a piece of the deployment handles the data: in its provider's words (`detail`),
    and the region it lies in, a code of REGIONS, ANYWHERE, or None when it lies in neither as
    far as this module knows."""

    detail: str
    region: str | None = None

    def __str__(self) -> str:
        if self.region in REGIONS:
            name = REGIONS[self.region]
            name = name[0].upper() + name[1:]
            return f"{name} ({self.detail})" if self.detail else name
        if self.region == ANYWHERE:
            return f"Anywhere ({self.detail})"
        return self.detail


EXPRESS_MODE = Place("Vertex AI's express mode", ANYWHERE)
VERTEX_GLOBAL = Place("Vertex AI's global endpoint", ANYWHERE)


def model_place(name: str, region: str, environ: Mapping[str, str]) -> Place | None:
    """Where the model `name` is run, as the settings and the environment its provider reads
    show it, for a deployment that keeps its data in `region` (empty for none); None when they
    do not show it."""
    prefix = model_provider(name)
    if (variable := MODEL_BASE_URLS.get(prefix)) and (url := environ.get(variable)):
        return endpoint_place(url, region)
    if prefix == "google":
        return Place("the Gemini Developer API", ANYWHERE)
    if prefix == "google-cloud":
        return _vertex(environ)
    if prefix == "anthropic":
        if inference_geo(name, region) == "us":
            return Place("Claude's US-only inference", "us")
        return Place("unless the Claude workspace keeps inference in the US", ANYWHERE)
    if prefix == "openrouter":
        if region:
            return Place(host_of(openrouter_url(region)), region)
        return Place("OpenRouter's providers", ANYWHERE)
    if prefix == "bedrock":
        return _bedrock(name.partition(":")[2], environ)
    return None


def endpoint_place(url: str, region: str) -> Place | None:
    """Where the endpoint at `url` runs, for the embeddings or a model whose address the
    environment moved; None when the host does not show it."""
    host = host_of(url)
    if on_own_network(host):
        return Place("the deployment's own network", region or None)
    if host == OPENAI_EU:
        return Place("OpenAI's European data residency", "eu")
    if match := re.fullmatch(r"(eu|us)\.openrouter\.ai", host):
        return Place(host, match[1])
    if host == "aiplatform.googleapis.com":
        return VERTEX_GLOBAL
    if match := re.fullmatch(r"aiplatform\.(eu|us)\.rep\.googleapis\.com", host):
        return _google_cloud(match[1])
    if match := re.fullmatch(r"([a-z0-9-]+)-aiplatform\.googleapis\.com", host):
        return _google_cloud(match[1])
    return None


def tracker_place(url: str, region: str) -> Place | None:
    """Where the error tracker at `url`, a SENTRY_DSN, keeps its reports; None when the host
    does not show it. Sentry's EU region takes them under ingest.de.sentry.io, in Germany, and
    its US region every other sentry.io host, the older ones without a region among them."""
    host = host_of(url)
    if on_own_network(host):
        return Place("the deployment's own network", region or None)
    if host.endswith(".ingest.de.sentry.io"):
        return Place("Sentry's EU region, Germany", "eu")
    if host == "sentry.io" or host.endswith(".sentry.io"):
        return Place("Sentry's US region", "us")
    return None


def inbound_place(settings: Settings, region: str) -> Place | None:
    """Where the provider that receives the projects' mail keeps it: SES in its queue's region,
    which its receipt rule's and the rule's bucket are in; None for Resend, whose receiving
    server is the domain's choice in its dashboard, and for a queue the address does not
    place."""
    if settings.receives_through != "ses":
        return None
    host = host_of(settings.inbound_mail_queue)
    if on_own_network(host):
        return Place("the deployment's own network", region or None)
    if match := re.fullmatch(r"sqs\.([a-z0-9-]+)\.amazonaws\.com", host):
        return _aws(match[1])
    return None


def bucket_place(storage: Storage, region: str) -> Place | None:
    """Where the storage keeps the files; None when the app cannot tell."""
    if storage.origin is None:
        return Place("the deployment's own disk", region or None)
    host = host_of(storage.origin)
    if on_own_network(host):
        return Place("the deployment's own network", region or None)
    if host.endswith(".amazonaws.com"):
        # Only an AWS bucket is asked: other services name their regions as they please, and
        # one on the deployment's own machines answers with whatever it was configured to say.
        location = storage.region
        return _aws(location) if location else None
    if match := re.search(r"(?:^|\.)([a-z]+[0-9])\.your-objectstorage\.com$", host):
        return Place(match[1], "eu" if match[1] in HETZNER_EU else None)
    if host.endswith(".eu.r2.cloudflarestorage.com"):
        return Place("R2's EU jurisdiction", "eu")
    return None


def _vertex(environ: Mapping[str, str]) -> Place:
    # As GoogleCloudProvider decides (pydantic_ai/providers/google_cloud.py): without a service
    # account's key file, a Gemini key makes it express mode, which names no location;
    # otherwise GOOGLE_CLOUD_LOCATION, us-central1 unless set.
    if not environ.get("GOOGLE_APPLICATION_CREDENTIALS") and (
        environ.get("GOOGLE_API_KEY") or environ.get("GEMINI_API_KEY")
    ):
        return EXPRESS_MODE
    return _google_cloud(environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1")


def _google_cloud(location: str) -> Place:
    if location == "global":
        return VERTEX_GLOBAL
    detail = f"the {location} multi-region" if location in ("eu", "us") else location
    if location in GOOGLE_CLOUD_EU:
        return Place(detail, "eu")
    if location == "us" or location.startswith("us-"):
        return Place(detail, "us")
    return Place(detail)


def _aws(location: str) -> Place:
    if location in AWS_EU:
        return Place(location, "eu")
    if location.startswith("us-"):
        return Place(location, "us")
    return Place(location)


def _bedrock(model: str, environ: Mapping[str, str]) -> Place | None:
    profile, dot, _ = model.partition(".")
    if dot and profile in BEDROCK_PROFILES:
        return Place(f"the {profile} inference profile", BEDROCK_PROFILES[profile])
    # A model id without a profile runs in the region the client is in.
    location = environ.get("AWS_DEFAULT_REGION")
    return _aws(location) if location else None


def residency_problems(
    settings: Settings, storage: Storage, environ: Mapping[str, str]
) -> list[str]:
    """What would handle the documents outside DATA_REGION, or where the settings cannot show,
    each as the step that fixes it; empty without a region and for a deployment that keeps it.

    The models, the embeddings endpoint and the bucket are checked, the pieces that receive the
    documents and what is read from them, and the provider that receives the mail sent to the
    projects' addresses, which becomes documents. The mail relay and the sign-in providers receive
    names and addresses, not documents, and the error tracker where the code failed and the
    ids it failed for (app/tracking.py), so they are left to the operator (deploying.md); the
    subprocessor list says where each of them is when the settings show it.
    """
    region = settings.data_region
    if not region:
        return []
    problems = []
    # A fallback is sent what its primary is, so it is held to the region too.
    models: dict[str, list[str]] = {}
    for model in settings.models:
        models.setdefault(model.name, []).append(model.variable)
    for name, variables in models.items():
        uses = f"{listing(variables)} {'uses' if len(variables) == 1 else 'use'}"
        if problem := _model_problem(uses, name, region, environ):
            problems.append(problem)
    if problem := _embeddings_problem(settings.embedding_base_url, region):
        problems.append(problem)
    if problem := _storage_problem(storage, region):
        problems.append(problem)
    if problem := _inbound_problem(settings, region):
        problems.append(problem)
    return problems


def _inbound_problem(settings: Settings, region: str) -> str | None:
    if not settings.receives_through:
        return None
    place = inbound_place(settings, region)
    if place is not None and place.region == region:
        return None
    where = REGIONS[region]
    receive = (
        f"receive it through Amazon SES in {where}, such as {AWS_EXAMPLES[region]}, its queue "
        "named in INBOUND_MAIL_QUEUE, or set INBOUND_DOMAIN empty to receive none"
    )
    if settings.receives_through == "resend":
        # Its receiving server is the domain's to choose in its dashboard, which the settings do
        # not show.
        return (
            "Resend receives the mail sent to the projects' addresses at INBOUND_DOMAIN, their "
            f"attachments among them, and the settings cannot show that it keeps them in "
            f"{where}; {receive}."
        )
    if place is None:
        return (
            f"INBOUND_MAIL_QUEUE is at {host_of(settings.inbound_mail_queue)}, and the app "
            f"cannot tell where the mail sent to the projects' addresses is kept; {receive}."
        )
    return (
        f"SES receives the mail sent to the projects' addresses in {place.detail}, which is "
        f"not in {where}; {receive}."
    )


def _model_problem(uses: str, name: str, region: str, environ: Mapping[str, str]) -> str | None:
    place = model_place(name, region, environ)
    if place is not None and place.region == region:
        return None
    where = REGIONS[region]
    prefix = model_provider(name)
    if prefix == "google":
        return (
            f"{uses} the Gemini Developer API (google:), which Google may run in any country "
            f"where it has facilities, so it cannot be kept in {where}; use the model through "
            f"Vertex AI as google-cloud:{name.partition(':')[2]}, with "
            f"GOOGLE_CLOUD_LOCATION={region} or another location in {where}."
        )
    if place == EXPRESS_MODE:
        return (
            f"{uses} Vertex AI in express mode (a GOOGLE_API_KEY without "
            "GOOGLE_APPLICATION_CREDENTIALS), which names no location; sign in with a service "
            f"account's key file instead and set GOOGLE_CLOUD_LOCATION={region} or another "
            f"location in {where}."
        )
    if prefix == "google-cloud" and place is not None:
        at = "its global endpoint" if place == VERTEX_GLOBAL else place.detail
        return (
            f"{uses} Vertex AI at {at} (GOOGLE_CLOUD_LOCATION, us-central1 unless set), which "
            f"is not in {where}; set GOOGLE_CLOUD_LOCATION to {region} or another location in "
            f"{where}, such as {GOOGLE_EXAMPLES[region]}."
        )
    if prefix == "anthropic":
        return (
            f"{uses} Claude through Anthropic's API, which runs inference in the United States "
            f"or anywhere, never in {where} alone; use Claude through Vertex AI (google-cloud:) "
            f"or Bedrock (bedrock:{region}.<model>) in {where}."
        )
    if prefix == "bedrock":
        held = (
            f"Bedrock's {place.detail}, which is not in {where}"
            if place is not None
            else "Bedrock without an inference profile or AWS_DEFAULT_REGION, so the app cannot "
            "tell which region runs it"
        )
        return (
            f"{uses} {held}; use {where}'s inference profile, bedrock:{region}.<model>, or a "
            f"model without a profile and AWS_DEFAULT_REGION in {where}, such as "
            f"{AWS_EXAMPLES[region]}."
        )
    url = environ.get(MODEL_BASE_URLS.get(prefix, ""), "")
    if region == "eu" and prefix.startswith("openai") and host_of(url) in ("", "api.openai.com"):
        return (
            f"{uses} OpenAI at api.openai.com, which does not keep it in {where}; create a "
            "project with European data residency and set "
            "OPENAI_BASE_URL=https://eu.api.openai.com/v1."
        )
    at = f" at {host_of(url)}" if url else ""
    return (
        f"{uses} {prefix}:{at}, and nothing in the settings says which region runs it, so it "
        f"cannot be kept in {where}; use a model the app can keep there: {CHOICES[region]}."
    )


def _embeddings_problem(url: str, region: str) -> str | None:
    place = endpoint_place(url, region)
    if place is not None and place.region == region:
        return None
    where = REGIONS[region]
    also = f", or OpenAI's at {OPENAI_EU} with European data residency" if region == "eu" else ""
    return (
        f"EMBEDDING_BASE_URL is at {host_of(url)}, which is not in {where} as far as the "
        "settings show; run the embeddings on the deployment's own network, as the compose "
        f"file's server does{also}."
    )


def _storage_problem(storage: Storage, region: str) -> str | None:
    place = bucket_place(storage, region)
    if place is not None and place.region == region:
        return None
    where = REGIONS[region]
    host = host_of(storage.origin or "")
    if host.endswith(".amazonaws.com"):
        if place is None:
            return (
                "The bucket in STORAGE_URL did not say which region it is in, so the app "
                f"cannot tell whether it is in {where}; allow the app s3:GetBucketLocation on "
                "it, which is how it asks (the log has the bucket's answer)."
            )
        return (
            f"The bucket in STORAGE_URL is in {place.detail}, which is not in {where}; keep the "
            f"files in a bucket in {where}, such as {AWS_EXAMPLES[region]}, and name it in "
            "STORAGE_URL."
        )
    others = ", on Hetzner (fsn1, nbg1, hel1), in R2's EU jurisdiction" if region == "eu" else ""
    return (
        f"The bucket in STORAGE_URL is at {host}, and the app cannot tell whether it keeps its "
        f"objects in {where}; use a bucket it can place: on AWS S3{others} or on the "
        "deployment's own network."
    )


def check_residency(settings: Settings, storage: Storage, environ: Mapping[str, str]) -> None:
    """Hold the deployment to DATA_REGION: in production, refuse to start while a piece would
    handle the documents elsewhere or the database holds organisations promised another region;
    in development, say so. Then record the region on the organisations that have none.
    """
    region = settings.data_region
    problems = residency_problems(settings, storage, environ)
    conflict = _conflicting_organizations(region)
    if conflict:
        problems.append(conflict)
    if problems and settings.environment == "production":
        raise RuntimeError(unkept(region, problems))
    for problem in problems:
        log.warning("%s", problem)
    # Only once nothing contradicts the region: an organisation stamped by a start that was
    # then refused would hold the operator to a region they had not settled on.
    if region and not conflict:
        _record_region(region)


def unkept(region: str, problems: list[str]) -> str:
    """The message a production process stops with when it cannot keep the region."""
    lines = "\n".join(f"  - {problem}" for problem in problems)
    if region:
        where = REGIONS[region]
        return f"DATA_REGION is {region}, but these would not keep the data in {where}:\n{lines}"
    return f"The deployment cannot keep the regions its organisations were promised:\n{lines}"


def _conflicting_organizations(region: str) -> str | None:
    """What stops the deployment from serving organisations whose records name a region other
    than `region`, or None when there are none."""
    with SessionLocal() as session:
        others = session.execute(
            select(Organization.region, func.count(), func.min(Organization.slug))
            .where(Organization.region.is_not(None), Organization.region != region)
            .group_by(Organization.region)
            .order_by(Organization.region)
        ).all()
    if not others:
        return None
    held = ", ".join(
        f"{count} in {REGIONS.get(code, code)} (such as {slug})" for code, count, slug in others
    )
    now = f"DATA_REGION is {region}" if region else "DATA_REGION is not set"
    return (
        f"The database holds organisations whose data was promised to another region, by "
        f"organizations.region: {held}. {now}; set it to the region they were promised, or "
        "point this deployment at its own region's database, since moving an organisation to "
        "another region is a migration of its data, not a setting."
    )


def _record_region(region: str) -> None:
    """Record `region` on the organisations created before the deployment named one."""
    with SessionLocal() as session:
        stamped = session.execute(
            update(Organization).where(Organization.region.is_(None)).values(region=region)
        ).rowcount
        session.commit()
    if stamped:
        which = "the organisation" if stamped == 1 else f"the {stamped} organisations"
        log.info("Recorded %s as the region of %s made before it was named.", region, which)
