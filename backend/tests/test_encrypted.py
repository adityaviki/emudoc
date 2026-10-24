"""The secrets the database keeps encrypted (app/keyring.py, app/encrypted.py): what a copy of
the database holds of an authenticator, the keys' rotation, the secrets from before, and a
member whose key is gone."""

import base64
import logging
import secrets

import pytest
from sqlalchemy import select, update

from app.api.auth import CODE_INVALID, CODE_UNCHECKABLE
from app.auth import totp_code, totp_step
from app.config import get_settings
from app.db import SessionLocal
from app.encrypted import (
    COMMAND,
    TOTP_SECRETS,
    check_encrypted,
    describe_encrypted,
    encrypt_secrets,
    keys,
)
from app.keyring import DEVELOPMENT_KEY, Keyring, Undecryptable, key_id, key_of, keyring
from app.maintenance import main
from app.models import User
from tests.test_auth import another_browser, login, signup

SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def fingerprint(key: str) -> str:
    return key_id(base64.b64decode(key))


@pytest.fixture
def keyed(monkeypatch):
    """Set ENCRYPTION_KEYS for the rest of the test, as a deployment's environment would."""

    def use(*keys: str) -> None:
        monkeypatch.setattr(get_settings(), "encryption_keys", ",".join(keys))

    return use


@pytest.fixture
def two_factor(client, signed_in_only, monkeypatch):
    """Sign Ada up and turn two-factor sign-in on; returns the secret and the recovery codes."""
    # Each test signs one account in several times, past what RATE_SIGN_INS lets it try in a
    # minute (tests/test_ratelimit.py).
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 100)

    def turn_on(email: str = "ada@example.com", browser=None) -> tuple[str, list[str]]:
        browser = browser or client
        signup(browser, email=email)
        secret = browser.post("/api/auth/mfa/setup").json()["secret"]
        code = totp_code(secret, totp_step())
        enabled = browser.post("/api/auth/mfa/enable", json={"code": code})
        assert enabled.status_code == 200, enabled.text
        return secret, enabled.json()["recovery_codes"]

    return turn_on


def stored(session, email: str = "ada@example.com") -> str:
    session.expire_all()
    return session.scalar(select(User.totp_secret).where(User.email == email))


def verify(client, code: str, email: str = "ada@example.com"):
    """Sign in from a new browser with the password, then the code."""
    browser = another_browser(client)
    assert login(browser, email) == {"mfa_required": True}
    return browser.post("/api/auth/mfa/verify", json={"code": code})


def following(secret: str) -> str:
    """The code the authenticator shows next, which no earlier sign-in has used."""
    return totp_code(secret, totp_step() + 1)


def moved_on(session) -> None:
    """As if the authenticator had moved on to a step no sign-in used yet."""
    session.execute(update(User).values(totp_last_step=None))
    session.commit()


# The cipher


def test_a_key_decrypts_only_what_it_encrypted_and_only_there():
    ring = keyring(new_key(), False)
    value = ring.encrypt(SECRET, "users.totp_secret:7")
    assert value.startswith(f"v1.{ring.current}.") and SECRET not in value
    # A fresh nonce each time: two rows with the same secret do not show it.
    assert ring.encrypt(SECRET, "users.totp_secret:7") != value
    assert ring.decrypt(value, "users.totp_secret:7") == SECRET

    with pytest.raises(Undecryptable, match="copied there from another place"):
        ring.decrypt(value, "users.totp_secret:8")
    tampered = value[:-2] + ("A" if value[-2] != "A" else "B") + value[-1]
    with pytest.raises(Undecryptable, match="it was changed"):
        ring.decrypt(tampered, "users.totp_secret:7")
    other = keyring(new_key(), False)
    with pytest.raises(Undecryptable, match=f"key {ring.current}, which ENCRYPTION_KEYS does not"):
        other.decrypt(value, "users.totp_secret:7")


def test_the_first_key_encrypts_and_every_key_decrypts_what_it_encrypted():
    old, new = new_key(), new_key()
    before = keyring(old, False).encrypt(SECRET, "users.totp_secret:1")
    rotated = keyring(f"{new},{old}", False)
    assert rotated.current == fingerprint(new) and rotated.holds(fingerprint(old))
    assert rotated.decrypt(before, "users.totp_secret:1") == SECRET
    assert key_of(rotated.encrypt(SECRET, "users.totp_secret:1")) == fingerprint(new)


def test_development_holds_its_own_key_after_the_others():
    development = key_id(DEVELOPMENT_KEY)
    assert keyring("", True).current == development
    own = new_key()
    assert keyring(own, True).current == fingerprint(own)
    assert keyring(own, True).holds(development)
    assert not keyring(own, False).holds(development)
    # Production refuses an empty setting before anything runs (tests/test_config.py), so an
    # empty one here is only ever a test's that set ENVIRONMENT by hand.
    assert keyring("", False).current == development


# What the database keeps


def test_the_database_keeps_no_secret_that_makes_the_codes(client, session, two_factor):
    secret, _ = two_factor()
    value = stored(session)
    assert value.startswith(f"v1.{keys().current}.") and secret not in value
    assert verify(client, following(secret)).status_code == 200


def test_a_secret_copied_into_another_account_makes_no_codes_there(
    client, session, two_factor, caplog
):
    ada, _ = two_factor()
    bob = another_browser(client)
    two_factor("bob@example.com", bob)
    session.execute(
        update(User).where(User.email == "bob@example.com").values(totp_secret=stored(session))
    )
    session.commit()

    refused = verify(client, following(ada), "bob@example.com")
    assert refused.status_code == 401 and refused.json()["detail"] == CODE_UNCHECKABLE
    bob_id = session.scalar(select(User.id).where(User.email == "bob@example.com"))
    assert f"User {bob_id}'s code could not be checked" in caplog.text
    assert "copied there from another place" in caplog.text
    # Ada's own still signs her in.
    moved_on(session)
    assert verify(client, following(ada)).status_code == 200


def test_a_key_is_rotated_by_putting_a_new_one_first(
    client, session, two_factor, keyed, capsys, caplog, monkeypatch
):
    old, new = new_key(), new_key()
    keyed(old)
    secret, _ = two_factor()
    assert key_of(stored(session)) == fingerprint(old)

    keyed(new, old)
    assert verify(client, following(secret)).status_code == 200
    with caplog.at_level(logging.INFO, "app.encrypted"):
        check_encrypted()
    assert (
        f"Secrets in the database are encrypted with key {fingerprint(new)}, the first of "
        "ENCRYPTION_KEYS." in caplog.text
    )
    assert (
        "1 authenticator secret is encrypted with an earlier key of ENCRYPTION_KEYS; run "
        f"`{COMMAND}` to encrypt it with the first key of ENCRYPTION_KEYS." in caplog.text
    )

    monkeypatch.setattr(get_settings(), "backup_days", 35)
    main(["encrypt-secrets"])
    assert capsys.readouterr().out.splitlines() == [
        f"Encrypted 1 authenticator secret of an earlier key with key {fingerprint(new)}, the "
        "first of ENCRYPTION_KEYS.",
        f"Every connector token is encrypted with key {fingerprint(new)} already.",
        f"Every webhook secret is encrypted with key {fingerprint(new)} already.",
        "Keep the other keys of ENCRYPTION_KEYS until no backup of the database holds what they "
        "encrypted, BACKUP_DAYS (35 days) from now, since a restore brings those values back as "
        "they were; then remove them.",
    ]
    assert key_of(stored(session)) == fingerprint(new)

    keyed(new)
    moved_on(session)
    assert verify(client, following(secret)).status_code == 200
    main(["encrypt-secrets"])
    assert capsys.readouterr().out.splitlines() == [
        f"Every authenticator secret is encrypted with key {fingerprint(new)} already.",
        f"Every connector token is encrypted with key {fingerprint(new)} already.",
        f"Every webhook secret is encrypted with key {fingerprint(new)} already.",
    ]


def test_a_secret_from_before_encryption_serves_until_the_command_encrypts_it(
    client, session, two_factor, caplog
):
    # As a checkout's database holds it from before 2026-10-20: base32, as the app showed it.
    secret, _ = two_factor()
    session.execute(update(User).values(totp_secret=secret))
    session.commit()
    assert verify(client, following(secret)).status_code == 200
    with caplog.at_level(logging.WARNING, "app.encrypted"):
        check_encrypted()
    assert "1 authenticator secret is kept as it was, unencrypted; run" in caplog.text

    done, _, _ = encrypt_secrets()
    assert (done.plain, done.earlier, done.lost.total()) == (1, 0, 0)
    assert describe_encrypted([done], keys(), None) == [
        f"Encrypted 1 authenticator secret kept as it was with key {keys().current}, the first "
        "of ENCRYPTION_KEYS."
    ]
    assert key_of(stored(session)) == keys().current
    moved_on(session)
    assert verify(client, following(secret)).status_code == 200


def test_a_member_whose_key_is_gone_is_told_to_sign_in_with_a_recovery_code(
    client, session, two_factor, keyed, capsys, caplog
):
    lost, kept = new_key(), new_key()
    keyed(lost)
    secret, recovery = two_factor()
    before = stored(session)
    keyed(kept)

    refused = verify(client, following(secret))
    assert refused.status_code == 401 and refused.json()["detail"] == CODE_UNCHECKABLE
    wrong = verify(client, "000000")
    assert wrong.json()["detail"] == CODE_UNCHECKABLE
    with caplog.at_level(logging.WARNING, "app.encrypted"):
        check_encrypted()
    said = (
        f"1 authenticator secret is encrypted with a key ENCRYPTION_KEYS does not hold "
        f"({fingerprint(lost)}), so its member cannot sign in with an authenticator app until "
        "the key is back in the list, after the first; without it, a member signs in with a "
        "recovery code and sets two-factor sign-in up again."
    )
    assert said in caplog.text

    # The command leaves it as it is, and says so.
    with pytest.raises(SystemExit) as failed:
        main(["encrypt-secrets"])
    assert failed.value.code == 1
    assert said in capsys.readouterr().out
    assert stored(session) == before

    # The way out: a recovery code, then the app set up afresh under the key there is.
    browser = another_browser(client)
    login(browser)
    assert browser.post("/api/auth/mfa/verify", json={"code": recovery[0]}).status_code == 200
    assert browser.post("/api/auth/mfa/disable", json={"code": recovery[1]}).status_code == 204
    fresh = browser.post("/api/auth/mfa/setup").json()["secret"]
    code = totp_code(fresh, totp_step())
    assert browser.post("/api/auth/mfa/enable", json={"code": code}).status_code == 200
    assert key_of(stored(session)) == fingerprint(kept)
    assert verify(client, following(fresh)).status_code == 200
    assert verify(client, "000000").json()["detail"] == CODE_INVALID


def test_the_command_keeps_an_authenticator_set_up_while_it_runs(
    client, session, two_factor, keyed, monkeypatch
):
    old, new = new_key(), new_key()
    keyed(old)
    two_factor()
    keyed(new, old)
    meanwhile = keyring(new, False).encrypt(SECRET, TOTP_SECRETS.place(1))
    encrypt = Keyring.encrypt

    def set_up_meanwhile(ring, text, place):
        # The member sets a new app up between the command's read and its write.
        with SessionLocal() as other:
            other.execute(update(User).values(totp_secret=meanwhile))
            other.commit()
        return encrypt(ring, text, place)

    monkeypatch.setattr(Keyring, "encrypt", set_up_meanwhile)
    done, _, _ = encrypt_secrets()
    assert (done.plain, done.earlier) == (0, 0)
    assert stored(session) == meanwhile
