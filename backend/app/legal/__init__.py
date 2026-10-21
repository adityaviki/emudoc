"""The deployment's terms of service and privacy policy, and whether a user has accepted them.

The two documents are Markdown templates beside this module, terms.md and privacy.md, filled
in as they are served (`GET /api/legal`, the /terms and /privacy pages). What they say about
the deployment is read from the settings and the app's own constants rather than written into
them, as the subprocessor list is (app/subprocessors.py): where the documents are kept, how
long each thing is kept on each plan, which cookies there are, whether a model provider may
learn from what it is sent, whether uploads are scanned. Who runs the deployment and how long
its backups and server logs are kept are the operator's word, OPERATOR_NAME, OPERATOR_EMAIL,
BACKUP_DAYS and SERVER_LOG_DAYS, which production requires; the texts themselves are a starting
point that the operator's own counsel reviews before launch.

TERMS_VERSION names the edition of both documents. A user accepts it at sign-up, or on the
page the app shows before anything else when they have not (an account made through Google or
Microsoft, or one that accepted an older edition); until then `get_usable_account` in
app/api/deps.py refuses the account everything but accepting, taking a copy of its data,
deleting itself and signing out. The local account of development accepts nothing. A change to
the templates that should ask everyone again moves TERMS_VERSION; tests/test_legal.py fails on
any change to them until that has been decided.
"""

import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from app.accounts import Account
from app.auth import BROWSER_COOKIE, BROWSER_KNOWN_FOR, CREDENTIAL_HISTORY, SESSION_COOKIE
from app.bounces import SUPPRESSED_FOR
from app.config import REGIONS, Settings, model_provider, parse_scanner_url
from app.entitlements import CHARS_PER_PAGE, PLANS, Entitlements
from app.export import EXPORT_DAYS
from app.network import MODEL_BASE_URLS, host_of, on_own_network
from app.notices import NOTIFICATION_DAYS
from app.oidc import OAUTH_COOKIE, OAUTH_WINDOW_SECONDS
from app.ratelimit import HISTORY
from app.retention import DELETION_GRACE, RETENTION_HELD, day
from app.storage import Storage
from app.trash import TRASH_DAYS
from app.worker import HOUSEKEEPING_INTERVAL

TERMS_VERSION = "2026-10-15"
TEMPLATES = Path(__file__).parent
# The model providers whose terms, as the app reaches them, forbid using what they are sent to
# train models: the paid Gemini API (production requires GOOGLE_PAID_TIER for google:),
# Vertex AI, Anthropic's and OpenAI's APIs and their clouds, and OpenRouter held to endpoints
# that keep nothing (DATA_POLICY in app/llm.py); and Ollama, whose default address is this
# machine. A provider whose address the environment moves (MODEL_BASE_URLS) counts only when
# the address is on the deployment's own network. The policy promises no more than this.
NO_TRAINING = frozenset(
    {
        "anthropic",
        "azure",
        "bedrock",
        "google",
        "google-cloud",
        "ollama",
        "openai",
        "openai-chat",
        "openai-responses",
        "openrouter",
    }
)
# The hosted embeddings endpoints whose terms forbid using what they are sent to train
# models, by host: OpenAI's API (and its European address), Azure OpenAI and Vertex AI. An
# endpoint on the deployment's own network sends nothing out; any other is promised nothing,
# since it is sent every passage.
NO_TRAINING_EMBEDDINGS = re.compile(
    r"(?:eu\.)?api\.openai\.com"
    r"|[a-z0-9-]+\.openai\.azure\.com"
    r"|(?:[a-z0-9-]+-)?aiplatform\.googleapis\.com"
    r"|aiplatform\.(?:eu|us)\.rep\.googleapis\.com"
)


def published() -> str:
    """The date of the current edition, as the documents and the app say it."""
    return day(datetime.fromisoformat(TERMS_VERSION).replace(tzinfo=UTC))


def terms_due(account: Account) -> bool:
    """Whether the account's user has yet to accept the current edition, which holds the API
    keys they made too. The local account of development, which has no session, never has to."""
    return not account.is_local and account.user.terms_version != TERMS_VERSION


def terms_pending() -> str:
    """What a request from an account that has not accepted the current edition is refused
    with, for whoever calls the API without the app, or with a page loaded before it changed."""
    return (
        f"Accept the terms of service and privacy policy of {published()} to go on using "
        "Emudoc: reload the page to read them."
    )


def terms_of_service(settings: Settings) -> str:
    return _fill(
        "terms.md",
        settings,
        deletion_days=str(DELETION_GRACE.days),
        held_days=str(RETENTION_HELD.days),
        chars_per_page=f"{CHARS_PER_PAGE:,}",
        paid_plans=_paid_plans(settings),
    )


def privacy_policy(
    settings: Settings, storage: Storage, environ: Mapping[str, str] = os.environ
) -> str:
    """The policy for the deployment `settings` and `storage` configure; `environ` is where the
    model libraries read an address that moves a provider, as for the subprocessor list."""
    return _fill(
        "privacy.md",
        settings,
        models=_models(settings, environ),
        region=_region(settings),
        chat_retention=_by_plan(lambda plan: plan.retention_days, "until deleted"),
        audit_retention=_by_plan(lambda plan: plan.audit_days, "as long as it exists"),
        held_days=str(RETENTION_HELD.days),
        credential_days=str(CREDENTIAL_HISTORY.days),
        browser_days=str(BROWSER_KNOWN_FOR.days),
        notification_days=str(NOTIFICATION_DAYS),
        trash_days=str(TRASH_DAYS),
        export_days=str(EXPORT_DAYS),
        # A count is pruned at the first housekeeping after it is HISTORY old.
        count_hours=str(round((HISTORY.total_seconds() + HOUSEKEEPING_INTERVAL) / 3600)),
        deletion_days=str(DELETION_GRACE.days),
        backups=_backups(settings.backup_days),
        server_logs=_server_logs(settings.server_log_days),
        session_cookie=SESSION_COOKIE,
        session_days=str(settings.session_days),
        oauth_cookie=OAUTH_COOKIE,
        oauth_minutes=str(OAUTH_WINDOW_SECONDS // 60),
        browser_cookie=BROWSER_COOKIE,
        third_parties=(
            "the pages load nothing from other sites but the images of document pages, which "
            "come straight from the file storage on the subprocessor list,"
            if storage.origin
            else "the pages load nothing from other sites,"
        ),
        # Each a bullet of its own when it is kept or done at all, joined so that none leaves a
        # blank line inside the list.
        more_security="\n".join(
            bullet
            for bullet in (
                _breach_check(settings.pwned_passwords_url),
                "- Every upload is scanned for malware before it is read, and one that is flagged "
                "is never served."
                if parse_scanner_url(settings.scanner_url)
                else "",
            )
            if bullet
        ),
        more_records="\n".join(
            bullet
            for bullet in (
                _connection_records(settings.access_log_days, storage),
                _error_reports(settings.sentry_dsn),
                _billing_records(settings),
                _undeliverable(settings),
            )
            if bullet
        ),
        more_retention="\n".join(
            bullet
            for bullet in (
                _connection_retention(settings.access_log_days),
                "- **Billing records**, as long as the organisation exists; the payment "
                "provider keeps the invoices as long as the law on accounts requires."
                if settings.stripe_secret_key
                else "",
                f"- **Addresses mail could not be delivered to**, {SUPPRESSED_FOR.days} days, "
                "or until the address's own user turns notices on again."
                if _hears_of_bounces(settings)
                else "",
            )
            if bullet
        ),
        billing_mail=(
            " An organisation's owners also get email about its subscription: when a payment "
            "failed, when its trial is about to end, when it was cancelled, and when it moved "
            "to a lower plan, for want of a payment or as a cancellation or a change ended "
            "the plan before."
            if settings.stripe_secret_key
            else ""
        ),
    )


def _fill(name: str, settings: Settings, **values: str) -> str:
    """The template, with what every document names: the edition, who runs the deployment and
    where to write to them. A setting that is not there reads as a note to set it, which only
    development can show, since production refuses to start without them."""
    operator = settings.operator_name.strip() or "[OPERATOR_NAME is not set]"
    email = settings.operator_email
    contact = f"[{email}](mailto:{email})" if email else "[OPERATOR_EMAIL is not set]"
    template = Template((TEMPLATES / name).read_text())
    return template.substitute(published=published(), operator=operator, contact=contact, **values)


def _models(settings: Settings, environ: Mapping[str, str]) -> str:
    """What the model providers, the embeddings endpoint's among them, may do with what they
    are sent, as far as the settings show."""
    # A fallback is sent what its primary is, so the promise holds it too.
    names = {model.name for model in settings.models}
    if not settings.google_paid_tier and any(model_provider(n) == "google" for n in names):
        return (
            "This deployment uses the free tier of Google's Gemini API, under which Google may "
            "use the documents and questions it receives to improve its products, and people "
            "at Google may read them."
        )
    if all(_trains_on_nothing(name, environ) for name in names) and _embeds_on_such_terms(
        settings.embedding_base_url
    ):
        return (
            "None of the AI model providers may use what it receives to train its models: each "
            "works for us under terms that forbid it."
        )
    return (
        "Whether an AI model provider may learn from what it receives is set by its own terms; "
        "ask us about them."
    )


def _trains_on_nothing(name: str, environ: Mapping[str, str]) -> bool:
    prefix = model_provider(name)
    if (variable := MODEL_BASE_URLS.get(prefix)) and (url := environ.get(variable)):
        return on_own_network(host_of(url))
    return prefix in NO_TRAINING


def _embeds_on_such_terms(url: str) -> bool:
    host = host_of(url)
    return on_own_network(host) or NO_TRAINING_EMBEDDINGS.fullmatch(host) is not None


def _server_logs(days: int | None) -> str:
    if days is None:
        return "as long as the servers keep them (SERVER_LOG_DAYS is not set)."
    return f"{days} {'day' if days == 1 else 'days'}."


def _connection_records(days: int | None, storage: Storage) -> str:
    """What the infrastructure records of connections, where ACCESS_LOG_DAYS says it keeps
    any: the network's flow logs, and the access logs of a bucket the browser fetches files
    from (Storage.origin), whose signed address names a download in its query."""
    if days is None:
        return ""
    files = (
        ", and for every file your browser fetches straight from the file storage (the images "
        "of document pages and the files you download) its IP address, the browser's "
        "description of itself, the time, the file's id and, for a download, its name"
        if storage.origin
        else ""
    )
    return (
        "- **Connection records**: the IP address of every connection to our servers, with its "
        f"time, ports and size{files}. We keep them to keep the service secure and to find "
        "faults, which is our legitimate interest. They never hold what a document, a question "
        "or an answer says."
    )


def _connection_retention(days: int | None) -> str:
    if days is None:
        return ""
    return f"- **Connection records**, {days} {'day' if days == 1 else 'days'}."


def _error_reports(dsn: str) -> str:
    """What the error tracker (app/tracking.py) is told, where SENTRY_DSN names one: by the
    company on the subprocessor list, or by the deployment itself on its own network."""
    if not dsn:
        return ""
    where = (
        "kept on our own servers"
        if on_own_network(host_of(dsn))
        else "sent to the error tracker on the subprocessor list"
    )
    return (
        "- **Error reports**: when the service runs into an error of its own, where in its code "
        "it happened and the ids of the request, account, organisation and project it happened "
        f"in, to find and fix the fault; they are {where}. They never hold what a document, a "
        "question or an answer says, nor an email address."
    )


def _paid_plans(settings: Settings) -> str:
    """What a paid plan is, where the deployment sells them (STRIPE_SECRET_KEY): how it is
    paid, renewed, ended and what a failed payment does, with the trial and the grace period
    the settings give (app/billing.py)."""
    if not settings.stripe_secret_key:
        return ""
    days = settings.stripe_trial_days
    trial = (
        f" An organisation's first subscription starts with a free trial of {days} "
        f"{'day' if days == 1 else 'days'}, and its first payment is taken when the trial ends, "
        "unless the subscription was cancelled before."
        if days
        else ""
    )
    grace = settings.billing_grace_days
    failed = (
        f"keeps its plan for {grace} {'day' if grace == 1 else 'days'} while the payment is "
        "tried again and its owners are told, and then moves to the Free plan"
        if grace
        else "moves to the Free plan at once, and its owners are told"
    )
    packs = (
        " An owner may also buy a pack of pages or questions, paid once, which adds to the "
        "organisation's allowance for the calendar month (in UTC) it is paid in and ends with "
        "it, used or not; it is not refunded either."
        if settings.stripe_packs
        else ""
    )
    return (
        "\nA paid plan is a subscription that an owner of the organisation buys for it, paid in "
        "advance for each period, a month or a year as chosen, through our payment provider on "
        "the [subprocessor list](/subprocessors), which keeps the payment details: we never see "
        "a card's number. The price is the one shown before paying, with the tax the law "
        f"requires added where it applies.{trial} The subscription renews at the end of each "
        "period until it is cancelled, which an owner can do at any time under Manage billing "
        "on the organisation page: it then runs to the end of the period paid for, and the "
        "organisation moves to the Free plan, keeping everything it holds; where that is more "
        "than the Free plan allows, it is read-only until it is within the plan again. A "
        "period begun is not refunded, unless the law requires it. If a payment fails, the "
        f"organisation {failed} in the same way, until the payment is made.{packs}\n"
    )


def _billing_records(settings: Settings) -> str:
    """What billing keeps, where the deployment sells its plans."""
    if not settings.stripe_secret_key:
        return ""
    return (
        "- **Billing**: for an organisation that buys a plan, its customer and subscription at "
        "our payment provider and what we last heard of them: whether the subscription is in "
        "its trial, paid or waiting for a payment, and until when; and the packs of pages or "
        "questions it bought, for which month. The provider keeps the "
        "payment details, the billing address and VAT id, and the invoices, with the email "
        "address of the owner who bought it; we never see a card's number. We need them to "
        "bill the organisation under its contract with us."
    )


def _hears_of_bounces(settings: Settings) -> bool:
    """Whether the mail provider tells the deployment which addresses to spare (app/bounces.py)."""
    return bool(settings.resend_webhook_secret or settings.mail_events_queue)


def _undeliverable(settings: Settings) -> str:
    """What is kept of an address the mail provider said not to write to, where it says so."""
    if not _hears_of_bounces(settings):
        return ""
    return (
        "- **Mail that could not be delivered**: when our email provider tells us that mail to "
        "an address bounced for good or that its reader marked it as spam, the address (as a "
        "hash only) and which of the two, so that we send it no notices."
    )


def _breach_check(url: str) -> str:
    """What the breached-password check (app/passwords.py) is told, where PWNED_PASSWORDS_URL
    names one: by the company on the subprocessor list, or by the deployment itself."""
    if not url:
        return ""
    how = (
        "its hash is looked up in a list kept on our own servers"
        if on_own_network(host_of(url))
        else "the service on the subprocessor list is sent the first five characters of its "
        "hash, which hundreds of passwords share, and never the password itself"
    )
    return f"- A new password is refused when it has appeared in a data breach: {how}."


def _region(settings: Settings) -> str:
    if code := settings.data_region:
        return (
            f"Your documents, and everything read from them, are kept and processed in "
            f"{REGIONS[code]}: our servers and database, the file storage and every AI model "
            "provider that receives them are there, and the service refuses to start "
            "otherwise. Email, sign-in through Google or Microsoft, the check of new passwords "
            "and error reports, which hold no documents, may be handled elsewhere; the "
            "subprocessor list says where, where it is known."
        )
    return (
        "This deployment does not keep the data in one region. The subprocessor list says where "
        "each company handles what it receives, where that is known. Where a company outside "
        "the European Economic Area receives personal data that European law protects, the "
        "transfer rests on that company's data processing terms, such as the European "
        "Commission's standard contractual clauses or the EU-US Data Privacy Framework."
    )


def _by_plan(days_of: Callable[[Entitlements], int | None], forever: str) -> str:
    """How long each plan keeps something, the plans with the same period together:
    "Free: 30 days; Pro: 365 days; Team and Enterprise: until deleted"."""
    plans: dict[int | None, list[str]] = {}
    for name, entitlements in PLANS.items():
        plans.setdefault(days_of(entitlements), []).append(name.title())
    return "; ".join(
        f"{_and(names)}: {forever if days is None else f'{days} days'}"
        for days, names in plans.items()
    )


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _backups(days: int | None) -> str:
    if days is None:
        return (
            "a deleted account stays in the backups of the database and the files until they "
            "expire (BACKUP_DAYS is not set)."
        )
    if days == 0:
        return "this deployment keeps none, so nothing of a deleted account outlives its deletion."
    return (
        f"the backups of the database and the files are kept for {days} days, so a deleted "
        f"account is gone from them {days} days after its deletion at the latest. Should a "
        "backup be restored before then, the account is deleted again."
    )
