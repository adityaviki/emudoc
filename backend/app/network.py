"""The deployment's own network, and what a connection that leaves it must do.

The app hands the documents to other processes over connections: the database, the bucket, the
malware scanner, the embeddings endpoint, the models and the mail relay. A connection inside
the deployment's own network (`on_own_network`: the loopback, a private or link-local address,
a compose service's name, a name under `OWN_SUFFIXES`) runs over machines and wires the
operator answers for, and encrypting it is the operator's call (deploying.md, "Encryption").
One that leaves it crosses networks nobody in the deployment controls, so it must be encrypted
and must check that the other end is the host it named, or anyone in the path could read it or
stand in for the server. Production refuses the settings that would send one otherwise:
`Settings.production_problems` (app/config.py) for what the settings show, the database, the
embeddings endpoint and the scanner, and `check_transport` (app/startup.py) for what only the
environment shows, a model's address and the bucket's.

The rules live here, apart from both, because app/config.py needs them before anything else of
the app can be imported; this module imports nothing of the app.
"""

import ipaddress
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

# Names that only resolve inside the deployment's own network.
OWN_SUFFIXES = (".internal", ".local", ".localhost", ".lan", ".home.arpa")
# The prefix a NAT64 gateway reaches an IPv4 address by, RFC 6052's.
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
# The providers whose address the environment can move, where a model may run on the
# deployment's own machines or on a service other than the one the prefix names.
MODEL_BASE_URLS = {
    "ollama": "OLLAMA_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "openai-chat": "OPENAI_BASE_URL",
    "openai-responses": "OPENAI_BASE_URL",
}
# The one sslmode that both encrypts the connection and checks that the server's certificate
# names the host: `require` encrypts to whoever answers, and `verify-ca` takes any server the
# authority signed for, such as another customer's database at the same provider.
VERIFIED = "verify-full"
# Where Linux distributions keep the system's bundle of certificate authorities: Debian's,
# Ubuntu's and Arch's, then Fedora's and RHEL's. The image sets SSL_CERT_FILE to the first.
SYSTEM_BUNDLES = (
    ("/etc/ssl/certs/ca-certificates.crt", "Debian, Ubuntu and Arch"),
    ("/etc/pki/tls/certs/ca-bundle.crt", "Fedora and RHEL"),
)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".")


def on_own_network(host: str) -> bool:
    """Whether `host` is reached inside the deployment's own network: a loopback, private or
    link-local address, a name without a dot (a compose service), or one under `OWN_SUFFIXES`."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(OWN_SUFFIXES)
    return address.is_private or address.is_loopback or address.is_link_local


def reaches_out(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether a connection to `address` leaves the deployment for the internet, which an address
    a member chose may (a webhook's, app/webhooks.py) where one of the own network may not: a
    global unicast address, an IPv6 one that carries an IPv4 address (mapped, NAT64's, 6to4's)
    judged by the IPv4 one it reaches. Stricter than `on_own_network`, which a setting the
    operator wrote is held to: the shared range of a carrier's NAT, the reserved and the
    multicast ones are no member's either."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address in _NAT64:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        elif address.sixtofour is not None:
            address = address.sixtofour
    return address.is_global and not address.is_multicast


def database_problem(url: str, variable: str = "DATABASE_URL") -> str | None:
    """What lets the URL in `variable` reach a database off the deployment's own network
    without encrypting the connection and checking the server's certificate, as the step that
    fixes it; None when every host it names is on the own network or the URL asks for both."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        # Not a URL at all; the engine says so when it is built.
        return None
    away = [host for host in _database_hosts(parsed) if not on_own_network(host)]
    modes = parsed.query.get("sslmode", ())
    modes = (modes,) if isinstance(modes, str) else modes
    if not away or (modes and all(mode == VERIFIED for mode in modes)):
        return None
    return (
        f"{variable} connects to {away[0]} without sslmode={VERIFIED}, so the connection "
        "may go unencrypted, or encrypted to a server that only claims to be the database, "
        "and the documents' text and the members' sign-ins with it; add "
        f"?sslmode={VERIFIED}&sslrootcert=system to the URL (with the path of the provider's "
        "certificate authority in place of system when a public one did not sign the "
        "server's), or reach the database on the deployment's own network."
    )


def trust_store_problem(
    variable: str, url: str, environ: Mapping[str, str], bundled_libpq: bool
) -> str | None:
    """What leaves a database URL that trusts the system's certificate authorities
    (`sslrootcert=system`) trusting none, as the step that fixes it; None when the URL names
    its authority otherwise, or the process can find the system's.

    libpq takes `system` to mean OpenSSL's default store, and the OpenSSL that psycopg's binary
    package brings (`bundled_libpq`) looks for it in a directory of the machine it was built
    on, which no other machine has: every certificate fails to verify, "certificate verify
    failed", while psql, with the system's libpq, connects. OpenSSL reads SSL_CERT_FILE and
    SSL_CERT_DIR in its place. PGSSLROOTCERT is libpq's own default for the parameter.
    """
    if not bundled_libpq:
        return None
    try:
        roots = make_url(url).query.get("sslrootcert", environ.get("PGSSLROOTCERT", ""))
    except ArgumentError:
        return None
    if "system" not in ((roots,) if isinstance(roots, str) else roots):
        return None
    named = [(name, environ.get(name, "")) for name in ("SSL_CERT_FILE", "SSL_CERT_DIR")]
    if os.path.isfile(named[0][1]) or os.path.isdir(named[1][1]):
        return None
    missing = [f"{name} names {path}, which is not there" for name, path in named if path]
    found = [path for path, _ in SYSTEM_BUNDLES if os.path.isfile(path)]
    if found:
        bundle = f"this machine's bundle, {found[0]}"
    else:
        bundle = "the system's bundle: " + ", ".join(
            f"{path} on {systems}" for path, systems in SYSTEM_BUNDLES
        )
    return (
        f"{variable} trusts the system's certificate authorities (sslrootcert=system), but "
        "psycopg's binary package looks for them in a directory of the machine it was built "
        "on, so every server's certificate would fail to verify"
        + (f" ({'; '.join(missing)})" if missing else "")
        + f"; set SSL_CERT_FILE to {bundle} (the image sets it)."
    )


def _database_hosts(url: URL) -> list[str]:
    """The hosts the URL connects to, its own and those a `host` parameter lists; a Unix
    socket's directory is this machine and is left out."""
    hosts = [url.host] if url.host else []
    listed = url.query.get("host", ())
    for value in (listed,) if isinstance(listed, str) else listed:
        for entry in value.split(","):
            entry = entry.strip()
            if not entry or entry.startswith("/"):
                continue
            if entry.startswith("["):
                hosts.append(entry[1:].partition("]")[0])
            else:
                # A name or an IPv4 address, with or without a port; a bare IPv6 address has
                # more than one colon and no port.
                hosts.append(entry.partition(":")[0] if entry.count(":") == 1 else entry)
    return hosts


def plain_http_problem(variable: str, url: str, sends: str) -> str | None:
    """What lets `url` carry `sends` to a host off the deployment's own network over plain
    HTTP, as the step that fixes it; None for HTTPS or the own network. Every HTTP client the
    app uses (httpx for the embeddings and the models, boto3 for the bucket) checks a
    server's certificate by default, so HTTPS is the whole requirement."""
    host = host_of(url)
    if urlsplit(url).scheme != "http" or not host or on_own_network(host):
        return None
    return (
        f"{variable} sends {sends} to {host} over plain HTTP; use its https:// address, or run "
        "the service on the deployment's own network."
    )


def scanner_problem(address: tuple[str, int] | str | None) -> str | None:
    """What lets the scanner SCANNER_URL names receive the uploads across a network the
    deployment does not own, as the step that fixes it; None for a socket, the own network or
    no scanner. clamd's protocol has no encryption to ask for."""
    if not isinstance(address, tuple) or on_own_network(address[0]):
        return None
    return (
        f"SCANNER_URL streams every upload to the ClamAV daemon at {address[0]}, and clamd's "
        "protocol has no encryption, so the files would cross a network the deployment does "
        "not own as they are; run the daemon on the deployment's own network, or reach it "
        "through an encrypted tunnel that listens there, such as WireGuard or stunnel."
    )
