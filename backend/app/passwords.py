"""New passwords checked against the passwords exposed in data breaches.

A password that was in a breach is on the lists a guesser tries first, however long it is, so
sign-up, a reset and a change refuse one (NIST SP 800-63B asks for this rather than rules about
the characters). The check is Have I Been Pwned's Pwned Passwords through its range API: the
service is sent the first five characters of the password's SHA-1, which hundreds of passwords
share, and answers with the rest of every breached hash that starts so; whether the password is
among them is decided here, so neither the password nor its hash leaves the process. The
request asks for padding, which fills the answer with entries of no count to a size that does
not depend on the prefix, so that someone watching the connection learns nothing from its size.
PWNED_PASSWORDS_URL names the service: Have I Been Pwned's own, or one on the deployment's own
network serving its downloadable list in the same form; empty checks nothing.

The check guards against a poor choice, not against an attack in progress, so a service that
does not answer within CHECK_TIMEOUT lets the password through and the log says so, rather
than keeping anyone from signing up or changing a password. Neither the log nor anything else
names the five characters (`bare_url` in app/logs.py withholds them from httpx's line).
"""

import hashlib
import logging

import httpx

from app.logs import bare_url

log = logging.getLogger(__name__)

CHECK_TIMEOUT = 5.0
BREACHED = (
    "This password has appeared in a data breach, so it is among the first that anyone guessing "
    "would try. Choose another one."
)


def breached(http: httpx.Client, url: str, password: str) -> bool:
    """Whether the range API at `url` lists the password as breached; False when `url` is empty
    or the service does not answer."""
    if not url:
        return False
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, rest = digest[:5], digest[5:]
    try:
        response = http.get(
            f"{url.rstrip('/')}/range/{prefix}",
            headers={"Add-Padding": "true"},
            timeout=CHECK_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        return _unanswered(url, f"it answered {error.response.status_code}")
    except httpx.HTTPError as error:
        # The message would name the address, prefix and all.
        return _unanswered(url, type(error).__name__)
    for line in response.text.splitlines():
        suffix, _, count = line.partition(":")
        if suffix.strip().upper() == rest:
            # A padding entry is a made-up suffix with no breach to its name.
            return count.strip() not in ("", "0")
    return False


def _unanswered(url: str, why: str) -> bool:
    log.warning(
        "The breached-password check at %s failed (%s), so a new password was taken without "
        "it; check PWNED_PASSWORDS_URL, or empty it to check nothing",
        bare_url(url.rstrip("/")),
        why,
    )
    return False
