"""The subprocessors: the companies a deployment hands its users' data to, read from its settings.

A subprocessor list is a promise to the organisations that upload their invoices and contracts,
and a hand-written one stops being true the first time a model, a bucket or a mail relay
changes. So the list is derived from the same settings the app sends data by, and published as
it stands (`GET /api/subprocessors`, the /subprocessors page). The settings cannot name who runs
the servers and the database; HOSTING_PROVIDER does, and production requires it.

A service on the deployment's own network is the deployment itself, not a subprocessor, and is
left out (`on_own_network` in app/residency.py). A host the list does not know the company of is
named as it is, so that nothing the app sends data to is missing from the page; an operator's
own server under a public name appears that way too.

Each entry says where the company handles what it receives, when the settings show it
(app/residency.py): the region the deployment keeps its data in (DATA_REGION) for who hosts it,
the bucket's region, the Vertex AI location, and so on. Production refuses to start under a
region while an entry that receives documents is outside it, so a page that names a region
keeps to it.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.config import Settings, model_provider
from app.oidc import providers
from app.residency import (
    MODEL_BASE_URLS,
    Place,
    bucket_place,
    endpoint_place,
    host_of,
    model_place,
    on_own_network,
)
from app.storage import Storage

# The companies behind the model providers, by the prefix of a provider:model name.
MODEL_PROVIDERS = {
    "anthropic": "Anthropic",
    "azure": "Microsoft (Azure OpenAI)",
    "bedrock": "Amazon Web Services (Bedrock)",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "google": "Google (Gemini API)",
    "google-cloud": "Google Cloud (Vertex AI)",
    "groq": "Groq",
    "mistral": "Mistral AI",
    "openai": "OpenAI",
    "openai-chat": "OpenAI",
    "openai-responses": "OpenAI",
    "openrouter": "OpenRouter",
    "xai": "xAI",
}
# The companies behind the hosts a bucket, an embeddings endpoint or a mail relay is commonly
# reached at, by the domain the host is under.
KNOWN_DOMAINS = {
    "amazonaws.com": "Amazon Web Services",
    "azure.com": "Microsoft Azure",
    "backblazeb2.com": "Backblaze",
    "brevo.com": "Brevo",
    "cloudflarestorage.com": "Cloudflare",
    "cohere.com": "Cohere",
    "digitaloceanspaces.com": "DigitalOcean",
    "fireworks.ai": "Fireworks AI",
    "gmail.com": "Google",
    "googleapis.com": "Google Cloud",
    "jina.ai": "Jina AI",
    "mailgun.org": "Mailgun",
    "mistral.ai": "Mistral AI",
    "office365.com": "Microsoft",
    "openai.com": "OpenAI",
    "openrouter.ai": "OpenRouter",
    "postmarkapp.com": "Postmark",
    "resend.com": "Resend",
    "sendgrid.net": "Twilio SendGrid",
    "together.xyz": "Together AI",
    "voyageai.com": "Voyage AI",
    "wasabisys.com": "Wasabi",
    "your-objectstorage.com": "Hetzner",
}

INDEXING = (
    "Reads documents to index them: it infers their sections, writes summaries, pulls out "
    "facts, and transcribes scanned pages and images.",
    "The text of documents and images of their scanned pages.",
)
ANSWERING = (
    "Answers the questions asked in the Ask tab.",
    "Questions, the project's name and description, and the passages, facts and outlines read "
    "to answer them.",
)
# OpenRouter passes each request on to a provider of the model, chosen under DATA_POLICY
# (app/llm.py), and the reader should know there is a second company behind it.
FORWARDED = (
    " OpenRouter forwards each request to one of the model's providers that neither stores it "
    "nor trains on it."
)


@dataclass(frozen=True)
class Subprocessor:
    """A company the deployment sends data to: who, what for, what it receives, and where it
    handles it when the settings show that."""

    name: str
    purpose: str
    data: str
    location: str | None = None


def subprocessors(
    settings: Settings,
    storage: Storage,
    environ: Mapping[str, str] = os.environ,
) -> list[Subprocessor]:
    """The companies the deployment configured by `settings` hands its users' data to: who
    hosts it, then the bucket (`storage`, by the origin its signed URLs lead to; a directory is
    the deployment's own), the models, the embeddings endpoint, the mail relay and the sign-in
    providers.

    `environ` is where the model libraries read an address that moves a provider
    (`MODEL_BASE_URLS`) and Vertex AI its location, after `export_env_file` has copied `.env`
    there in development.
    """
    region = settings.data_region
    found: list[Subprocessor] = []
    if hosting := settings.hosting_provider.strip():
        # The one place the settings cannot show: DATA_REGION is the operator's word for it.
        where = str(Place("", region)) if region else None
        found.append(
            Subprocessor(
                hosting,
                "Runs the app's servers and its database.",
                "Everything the app keeps: documents, questions and answers, and accounts.",
                where,
            )
        )
    if storage.origin and (name := _company(storage.origin)):
        found.append(
            Subprocessor(
                name,
                "Keeps the uploaded files and the page images rendered from them.",
                "Uploaded documents.",
                _where(bucket_place(storage, region)),
            )
        )
    found += _models(settings, environ)
    if name := _company(settings.embedding_base_url):
        found.append(
            Subprocessor(
                name,
                "Search by meaning: turns each passage into a vector as documents are indexed, "
                "and each search into one.",
                "Passages of document text, and search queries.",
                _where(endpoint_place(settings.embedding_base_url, region)),
            )
        )
    if settings.smtp_url and (name := _company(settings.smtp_url)):
        found.append(
            Subprocessor(
                name,
                "Sends the emails that verify an address and reset a password, and the "
                "operator's alerts.",
                "Members' names and email addresses, and in the alerts the names of "
                "organisations, projects and files.",
            )
        )
    for provider in providers(settings).values():
        found.append(
            Subprocessor(
                provider.label,
                f"Signs in the members who choose to sign in with {provider.label}.",
                "Confirms a member's name and email address. It receives no documents.",
            )
        )
    return found


def _models(settings: Settings, environ: Mapping[str, str]) -> list[Subprocessor]:
    """One entry per company behind INDEX_MODEL and AGENT_MODEL, both roles in one entry when
    a company serves both."""
    roles: dict[str, list[tuple[str, str]]] = {}
    places: dict[str, list[str]] = {}
    # Only the openrouter: adapter sends the data policy; another prefix pointed at OpenRouter's
    # address reaches it without one, and the page must not say otherwise.
    forwarded: dict[str, bool] = {}
    for model, role in ((settings.index_model, INDEXING), (settings.agent_model, ANSWERING)):
        prefix = model_provider(model)
        if (variable := MODEL_BASE_URLS.get(prefix)) and (url := environ.get(variable)):
            name = _company(url)
        else:
            name = MODEL_PROVIDERS.get(prefix, prefix)
        if not name:
            continue
        if role not in roles.setdefault(name, []):
            roles[name].append(role)
        # Two models of one company may run in different places, such as two Bedrock profiles.
        where = _where(model_place(model, settings.data_region, environ))
        if where and where not in places.setdefault(name, []):
            places[name].append(where)
        forwarded[name] = forwarded.get(name, True) and prefix == "openrouter"
    found = []
    for name, served in roles.items():
        purpose = " ".join(purpose for purpose, _ in served)
        if forwarded[name]:
            purpose += FORWARDED
        data = " ".join(data for _, data in served)
        found.append(Subprocessor(name, purpose, data, "; ".join(places.get(name, [])) or None))
    return found


def _where(place: Place | None) -> str | None:
    return str(place) if place is not None else None


def _company(url: str) -> str | None:
    """The company behind the host `url` names, the host itself when it is not one the list
    knows, or None when the host is on the deployment's own network."""
    host = host_of(url)
    if not host or on_own_network(host):
        return None
    for domain, company in KNOWN_DOMAINS.items():
        if host == domain or host.endswith(f".{domain}"):
            return company
    return host
