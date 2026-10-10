import json

import pytest

from redblue.core import passkeys as pk
from redblue.core.auth import Role, create_user
from redblue.core.config import Settings
from redblue.core.db import Database
from redblue.core.testing_webauthn import SoftAuthenticator

ORIGIN = "https://site.example"


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url="sqlite://", env="test", base_url=ORIGIN, storage_dir=tmp_path)


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        yield s


@pytest.fixture
def user(db):
    return create_user(db, "w@example.com", None, Role.writer)


def _register(db, settings, user, device=None):
    device = device or SoftAuthenticator(ORIGIN, "site.example")
    options, token = pk.registration_options(db, settings, user)
    passkey = pk.finish_registration(db, settings, user, token, device.create(options), "Laptop")
    return device, passkey


def test_register_then_sign_in_without_a_username(db, settings, user):
    device, passkey = _register(db, settings, user)
    assert passkey.name == "Laptop" and passkey.user_id == user.id
    options, token = pk.authentication_options(db, settings)
    assert '"allowCredentials": []' in options and '"userVerification": "required"' in options
    assert pk.finish_authentication(db, settings, token, device.get(options)) == user
    assert passkey.sign_count == 1 and passkey.last_used_at is not None


def test_a_users_passkeys_share_one_handle_and_cannot_be_added_twice(db, settings, user):
    first, a = _register(db, settings, user)
    _, b = _register(db, settings, user)
    assert a.user_handle == b.user_handle
    options, token = pk.registration_options(db, settings, user)
    excluded = {c["id"] for c in json.loads(options)["excludeCredentials"]}
    assert excluded == {a.credential_id, b.credential_id}  # the browser skips these devices
    with pytest.raises(pk.PasskeyError, match="already registered"):
        pk.finish_registration(db, settings, user, token, first.create(options), "Again")


def test_a_challenge_works_once(db, settings, user):
    device, _ = _register(db, settings, user)
    options, token = pk.authentication_options(db, settings)
    response = device.get(options)
    pk.finish_authentication(db, settings, token, response)
    with pytest.raises(pk.PasskeyError, match="expired"):
        pk.finish_authentication(db, settings, token, response)  # replay


def test_wrong_origin_is_refused(db, settings, user):
    device, _ = _register(db, settings, user)
    options, token = pk.authentication_options(db, settings)
    with pytest.raises(pk.PasskeyError, match="could not be verified"):
        pk.finish_authentication(
            db, settings, token, device.get(options, origin="https://evil.test")
        )


def test_user_verification_is_required(db, settings, user):
    device = SoftAuthenticator(ORIGIN, "site.example", user_verified=False)
    options, token = pk.registration_options(db, settings, user)
    with pytest.raises(pk.PasskeyError, match="could not be verified"):
        pk.finish_registration(db, settings, user, token, device.create(options), "No PIN")


def test_user_handle_must_match_the_passkey(db, settings, user):
    device, _ = _register(db, settings, user)
    options, token = pk.authentication_options(db, settings)
    with pytest.raises(pk.PasskeyError, match="isn't registered"):
        pk.finish_authentication(db, settings, token, device.get(options, user_handle=b"x" * 32))


def test_unknown_passkeys_and_inactive_users_cannot_sign_in(db, settings, user):
    stranger = SoftAuthenticator(ORIGIN, "site.example")
    options, token = pk.authentication_options(db, settings)
    with pytest.raises(pk.PasskeyError, match="isn't registered"):
        pk.finish_authentication(db, settings, token, stranger.get(options, user_handle=b"u"))
    device, _ = _register(db, settings, user)
    user.is_active = False
    options, token = pk.authentication_options(db, settings)
    with pytest.raises(pk.PasskeyError):
        pk.finish_authentication(db, settings, token, device.get(options))


def test_registration_challenge_is_bound_to_its_user(db, settings, user):
    other = create_user(db, "o@example.com", None, Role.writer)
    device = SoftAuthenticator(ORIGIN, "site.example")
    options, token = pk.registration_options(db, settings, user)
    with pytest.raises(pk.PasskeyError, match="another session"):
        pk.finish_registration(db, settings, other, token, device.create(options), "Stolen")


def test_people_remove_only_their_own_passkeys(db, settings, user):
    _, passkey = _register(db, settings, user)
    other = create_user(db, "o@example.com", None, Role.writer)
    assert not pk.remove_passkey(db, other, passkey.id)
    assert pk.remove_passkey(db, user, passkey.id)
    assert pk.user_passkeys(db, user) == []
