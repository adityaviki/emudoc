"""The keys the app encrypts what it keeps for its members with, and the cipher.

Some of what the database holds would let whoever copies it act as a member: an authenticator's
secret makes that member's codes. Such values are encrypted with a key the database never holds
(ENCRYPTION_KEYS, from the environment), so that a dump or a backup of the database is not
enough; app/encrypted.py names the columns and keeps them so.

AES-256-GCM, a fresh 96-bit nonce per value, and the value's place (its table, column and row)
as associated data, so that a value copied into another row or column fails to decrypt instead
of serving there. A value names the key that encrypted it by the key's fingerprint, the first
eight hex digits of its SHA-256, which says nothing of the key: several keys can be held at
once, the first encrypting and every one decrypting what it encrypted, which is how a key is
rotated (deploying.md, "The encryption keys").

Imports nothing of the app but its errors: config.py checks the setting with it.
"""

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.errors import AppError

KEY_BYTES = 32
NONCE_BYTES = 12
# What an encrypted value starts with: the format's version, then the key's fingerprint. An
# authenticator's secret as it was kept before is base32, capitals and digits, so it never does.
VERSION = "v1"
PREFIX = f"{VERSION}."
# Development's key, which anyone reading the code knows, like the compose database's password:
# a checkout runs without choosing one, and production refuses it.
DEVELOPMENT_KEY = hashlib.sha256(b"Emudoc's development key, which anyone can read").digest()
# How an operator makes a key.
NEW_KEY = "`openssl rand -base64 32`"
KEY_FORM = (
    f"each key is {KEY_BYTES} random bytes in base64, as {NEW_KEY} prints one, and the keys "
    "are separated by commas"
)


class Undecryptable(AppError):
    """A value that none of the keys held decrypts: the key that encrypted it is not among
    them, or the value was changed or copied from another place. The message names the key by
    its fingerprint alone."""

    def __init__(self, message: str, key_id: str):
        super().__init__(message)
        self.key_id = key_id


def key_id(key: bytes) -> str:
    """The key's fingerprint, which the values it encrypted carry."""
    return hashlib.sha256(key).hexdigest()[:8]


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def key_of(value: str) -> str | None:
    """The fingerprint of the key that encrypted the value; None for a value kept as it was."""
    return value.split(".")[1] if is_encrypted(value) else None


def parse_keys(value: str) -> list[bytes]:
    """The keys ENCRYPTION_KEYS lists, the first the one that encrypts; empty when it lists
    none. A mistake is a `ValueError` that says which entry by its place, never what it holds,
    since the sentence is printed where a key must not be."""
    keys: list[bytes] = []
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    for place, entry in enumerate(entries, 1):
        key = _decoded(entry)
        which = f"Entry {place} of ENCRYPTION_KEYS"
        if key is None:
            raise ValueError(f"{which} is not base64: {KEY_FORM}.")
        if len(key) != KEY_BYTES:
            raise ValueError(f"{which} is {len(key)} bytes, not {KEY_BYTES}: {KEY_FORM}.")
        if any(key_id(key) == key_id(earlier) for earlier in keys):
            raise ValueError(f"{which} is a key listed before it; list each key once.")
        keys.append(key)
    return keys


@dataclass(frozen=True)
class Keyring:
    """The keys by their fingerprints, in the order listed: the first encrypts, and each one
    decrypts what it encrypted."""

    ciphers: tuple[tuple[str, AESGCM], ...]

    @property
    def current(self) -> str:
        """The fingerprint of the key that encrypts."""
        return self.ciphers[0][0]

    def holds(self, fingerprint: str) -> bool:
        return any(name == fingerprint for name, _ in self.ciphers)

    def encrypt(self, text: str, place: str) -> str:
        """`text` encrypted for `place`, which only the same place decrypts it for."""
        nonce = os.urandom(NONCE_BYTES)
        sealed = self.ciphers[0][1].encrypt(nonce, text.encode(), place.encode())
        body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
        return f"{PREFIX}{self.current}.{body}"

    def decrypt(self, value: str, place: str) -> str:
        """What `encrypt` was given for `place`; `Undecryptable` when no key held decrypts it
        there."""
        fingerprint = key_of(value) or ""
        cipher = next((cipher for name, cipher in self.ciphers if name == fingerprint), None)
        if cipher is None:
            raise Undecryptable(
                f"{place} is encrypted with key {fingerprint}, which ENCRYPTION_KEYS does not "
                "hold; put that key back in the list, after the first.",
                fingerprint,
            )
        try:
            raw = _decoded(value.split(".", 2)[2]) or b""
            text = cipher.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], place.encode())
        except (IndexError, InvalidTag, ValueError):
            raise Undecryptable(
                f"{place} does not decrypt with key {fingerprint}, which encrypted it: it was "
                "changed, or copied there from another place.",
                fingerprint,
            ) from None
        return text.decode()


@lru_cache
def keyring(setting: str, development: bool) -> Keyring:
    """The keys of ENCRYPTION_KEYS. An empty setting is development's key alone, which
    production refuses before anything runs (`Settings.production_problems`). Development also
    holds its own key after the others, so that a checkout's database keeps working once a key
    of its own is set, until `encrypt-secrets` has encrypted it afresh."""
    keys = parse_keys(setting)
    if (development or not keys) and DEVELOPMENT_KEY not in keys:
        keys.append(DEVELOPMENT_KEY)
    return Keyring(tuple((key_id(key), AESGCM(key)) for key in keys))


def _decoded(text: str) -> bytes | None:
    """Base64 in either alphabet, padded or not."""
    text = text.replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
