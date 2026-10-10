"""Passkeys (WebAuthn): passwordless sign-in for the admin.

Cryptographic verification is done by py_webauthn. This module stores credentials and runs
the two ceremonies around it:

* Each ceremony starts with a challenge stored server-side (``rb_passkey_challenges``) and
  identified by an unguessable token in an HttpOnly cookie. Finishing consumes the row, so
  a challenge works once. Synced passkeys report a signature counter of 0, so the counter
  alone cannot stop a replayed sign-in.
* Passkeys must be discoverable and user-verified (PIN, fingerprint, face): a passkey
  replaces the password, so the device has to check the person too.
* The relying party is the host of ``RB_BASE_URL``; only that origin is accepted.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    delete,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    generate_user_handle,
    parse_authentication_credential_json,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from redblue.core.auth import User
from redblue.core.config import Settings
from redblue.core.db import Base, utcnow

CHALLENGE_COOKIE = "rb_passkey"
CHALLENGE_TTL = timedelta(minutes=5)
MAX_PASSKEYS_PER_USER = 20


class PasskeyError(Exception):
    """A ceremony failed. The message is safe to show to the person signing in."""


class Passkey(Base):
    __tablename__ = "rb_passkeys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("rb_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, index=True)  # base64url
    public_key: Mapped[bytes] = mapped_column(LargeBinary)  # COSE key
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    user_handle: Mapped[str] = mapped_column(String(128))  # base64url, same for a user's passkeys
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)  # synced (e.g. iCloud)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(User)


class PasskeyChallenge(Base):
    __tablename__ = "rb_passkey_challenges"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16))  # "register" | "login"
    challenge: Mapped[str] = mapped_column(String(128))  # base64url
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


def relying_party(settings: Settings) -> tuple[str, str]:
    """``(rp_id, origin)`` from ``RB_BASE_URL``."""
    parts = urlsplit(settings.base_url)
    if not parts.hostname:
        raise PasskeyError("RB_BASE_URL has no host, so passkeys can't be used.")
    return parts.hostname, f"{parts.scheme}://{parts.netloc}"


def user_passkeys(db: Session, user: User) -> list[Passkey]:
    return list(db.scalars(select(Passkey).where(Passkey.user_id == user.id).order_by(Passkey.id)))


def _new_challenge(
    db: Session, purpose: str, challenge: bytes, user: User | None = None, handle: str = ""
) -> str:
    now = utcnow()
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < now))
    token = secrets.token_urlsafe(32)
    db.add(
        PasskeyChallenge(
            token=token,
            purpose=purpose,
            challenge=bytes_to_base64url(challenge),
            user_id=user.id if user else None,
            user_handle=handle or None,
            expires_at=now + CHALLENGE_TTL,
        )
    )
    db.flush()
    return token


def _take_challenge(db: Session, token: str | None, purpose: str) -> PasskeyChallenge:
    """Load and delete the challenge: each one can finish a ceremony once."""
    row = db.get(PasskeyChallenge, token) if token else None
    if row is None:
        raise PasskeyError("This passkey request has expired. Please try again.")
    db.delete(row)
    db.flush()
    if row.purpose != purpose or row.expires_at < utcnow():
        raise PasskeyError("This passkey request has expired. Please try again.")
    return row


# ---------------------------------------------------------------- registration


def registration_options(db: Session, settings: Settings, user: User) -> tuple[str, str]:
    """``(options JSON for navigator.credentials.create, challenge token)``."""
    existing = user_passkeys(db, user)
    if len(existing) >= MAX_PASSKEYS_PER_USER:
        raise PasskeyError(f"You already have {MAX_PASSKEYS_PER_USER} passkeys. Remove one first.")
    rp_id, _ = relying_party(settings)
    handle = existing[0].user_handle if existing else bytes_to_base64url(generate_user_handle())
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.site_name,
        user_name=user.email,
        user_id=base64url_to_bytes(handle),
        user_display_name=user.name or user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in existing
        ],
    )
    token = _new_challenge(db, "register", options.challenge, user, handle)
    return options_to_json(options), token


def finish_registration(
    db: Session, settings: Settings, user: User, token: str | None, credential: dict, name: str
) -> Passkey:
    row = _take_challenge(db, token, "register")
    if row.user_id != user.id:
        raise PasskeyError("This passkey request belongs to another session. Please try again.")
    rp_id, origin = relying_party(settings)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(row.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        raise PasskeyError("The passkey could not be verified.") from exc
    credential_id = bytes_to_base64url(verified.credential_id)
    if db.scalar(select(Passkey.id).where(Passkey.credential_id == credential_id)):
        raise PasskeyError("This passkey is already registered.")
    passkey = Passkey(
        user_id=user.id,
        name=(name.strip() or "Passkey")[:100],
        credential_id=credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        user_handle=row.user_handle or "",
        backed_up=verified.credential_backed_up,
    )
    db.add(passkey)
    db.flush()
    return passkey


# ---------------------------------------------------------------- sign-in


def authentication_options(db: Session, settings: Settings) -> tuple[str, str]:
    """``(options JSON for navigator.credentials.get, challenge token)``. No user is named:
    the browser offers the passkeys it has for this site."""
    rp_id, _ = relying_party(settings)
    options = generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED
    )
    return options_to_json(options), _new_challenge(db, "login", options.challenge)


def finish_authentication(
    db: Session, settings: Settings, token: str | None, credential: dict
) -> User:
    row = _take_challenge(db, token, "login")
    failed = PasskeyError("That passkey isn't registered here, or it could not be verified.")
    try:
        parsed = parse_authentication_credential_json(credential)
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        raise failed from exc
    passkey = db.scalar(
        select(Passkey).where(Passkey.credential_id == bytes_to_base64url(parsed.raw_id))
    )
    if passkey is None:
        raise failed
    handle = parsed.response.user_handle
    if handle is not None and bytes_to_base64url(handle) != passkey.user_handle:
        raise failed
    rp_id, origin = relying_party(settings)
    try:
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=base64url_to_bytes(row.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
        raise failed from exc
    user = passkey.user
    if not user.is_active:
        raise failed
    passkey.sign_count = verified.new_sign_count
    passkey.backed_up = verified.credential_backed_up
    passkey.last_used_at = utcnow()
    db.flush()
    return user


def remove_passkey(db: Session, user: User, passkey_id: int) -> bool:
    passkey = db.get(Passkey, passkey_id)
    if passkey is None or passkey.user_id != user.id:
        return False
    db.delete(passkey)
    db.flush()
    return True
