"""A software WebAuthn authenticator for tests: makes the responses a browser would send for
``navigator.credentials.create`` / ``.get``, signed with a real P-256 key. Test-only."""

from __future__ import annotations

import hashlib
import json
import secrets
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, encode_cbor

FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT = 0x01, 0x04, 0x08, 0x10, 0x40


class SoftAuthenticator:
    def __init__(self, origin: str, rp_id: str, *, user_verified: bool = True):
        self.origin, self.rp_id = origin, rp_id
        self.user_verified = user_verified
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.user_handle: bytes | None = None
        self.sign_count = 0

    def _flags(self, extra: int = 0) -> int:
        return FLAG_UP | (FLAG_UV if self.user_verified else 0) | extra

    def _auth_data(self, flags: int, attested: bytes = b"") -> bytes:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack(">I", self.sign_count) + attested

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        data = {"type": kind, "challenge": challenge, "origin": origin or self.origin}
        return json.dumps(data, separators=(",", ":")).encode()

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return encode_cbor(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    def create(self, options_json: str, *, origin: str | None = None) -> dict:
        """The credential JSON a browser posts after ``navigator.credentials.create``."""
        options = json.loads(options_json)
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        attested = (
            bytes(16)  # AAGUID
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        auth_data = self._auth_data(self._flags(FLAG_AT), attested)
        attestation = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self, options_json: str, *, origin: str | None = None, user_handle: bytes | None = None
    ) -> dict:
        """The credential JSON a browser posts after ``navigator.credentials.get``."""
        options = json.loads(options_json)
        self.sign_count += 1
        auth_data = self._auth_data(self._flags())
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        cid = bytes_to_base64url(self.credential_id)
        handle = user_handle if user_handle is not None else self.user_handle
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(handle) if handle else None,
            },
            "clientExtensionResults": {},
        }
