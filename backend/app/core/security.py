import base64
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from backend.app.core.config import settings
from backend.app.core.exceptions import InvalidTokenError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import JWTError, jwt
from passlib.context import CryptContext

# Password Hashing with Argon2 and Bcrypt
# Argon2id first: passlib's bcrypt backend is unusable with bcrypt>=5 (detection
# crashes in `detect_wrap_bug`), so hashes are minted via argon2id and bcrypt is
# kept only to verify legacy bcrypt-styled hashes.
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain-text password against a hashed string."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generates a secure password hash."""
    return pwd_context.hash(password)


# JWT Token Generation & Verification
#
# Tokens are always signed with a symmetric HMAC algorithm, because the key is
# a single shared secret (_signing_key). An asymmetric setting such as RS256
# would need a private key this app does not have, so it is rejected rather
# than accepted and then failing at the first sign/verify.
_JWT_HMAC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


def _algorithm() -> str:
    """Return the JWT signing algorithm, validated.

    Reads ``settings.JWT_ALGORITHM``. This used to be a module-level literal
    ``ALGORITHM = "HS256"`` that no setting could influence, so JWT_ALGORITHM
    was accepted from the environment and never used â€” the same write-only
    pattern as JWT_SECRET_KEY. pyproject.toml already described HS256 as coming
    from JWT_ALGORITHM, which is now true.

    Read at call time so settings overrides in tests take effect.
    """
    configured = (settings.JWT_ALGORITHM or "HS256").strip().upper()
    if configured not in _JWT_HMAC_ALGORITHMS:
        raise ValueError(
            f"JWT_ALGORITHM={settings.JWT_ALGORITHM!r} is not supported. This app "
            f"signs with a shared secret, so only HMAC algorithms apply: "
            f"{', '.join(sorted(_JWT_HMAC_ALGORITHMS))}."
        )
    return configured


def _signing_key() -> str:
    """Return the key JWTs are signed and verified with.

    Every token in this module reads the key through this one function so there
    is a single place that decides which secret applies.

    It resolves to ``settings.JWT_SECRET_KEY``, which the settings validator
    defaults to ``SECRET_KEY`` when unset. Reading ``SECRET_KEY`` directly here
    instead made ``JWT_SECRET_KEY`` a write-only setting: it was accepted from
    the environment, documented in both .env.example files, and then never used,
    so changing it had no effect whatsoever. Routing through it also means the
    token-signing key can be rotated independently of the other secrets derived
    from ``SECRET_KEY``.

    Read at call time, not import time, so tests that override settings still
    take effect.
    """
    return settings.JWT_SECRET_KEY or settings.SECRET_KEY


def create_access_token(
    subject: str | UUID,
    role: str | None = None,
    expires_delta: timedelta | None = None,
    token_type: str = "access",
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """Creates a short-lived access token (default 15 minutes).

    Accepts an optional `role`, a `token_type` (defaults to "access"), and
    arbitrary `additional_claims` merged into the JWT payload, so callers
    declare identity claims through one function. Identity and lifetime claims
    (including ``type``) can never be overridden via ``additional_claims``.
    """
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": secrets.token_hex(16),
        "type": token_type,
    }
    if role is not None:
        to_encode["role"] = role
    if additional_claims:
        # Identity and lifetime claims are controlled exclusively by this
        # function. Callers must not override subject, expiry, token type, or
        # token identifier through the extension-claims parameter.
        reserved_claims = {"sub", "exp", "iat", "jti", "type", "iss", "aud"}
        collisions = reserved_claims.intersection(additional_claims)
        if collisions:
            raise ValueError(
                "additional_claims cannot override reserved JWT claims: "
                + ", ".join(sorted(collisions))
            )
        to_encode.update(additional_claims)
    return jwt.encode(to_encode, _signing_key(), algorithm=_algorithm())


def create_refresh_token(subject: str | UUID, expires_delta: timedelta | None = None) -> str:
    """Creates a long-lived refresh token (default 7 days)."""
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": secrets.token_hex(16),
        "type": "refresh"
    }
    return jwt.encode(to_encode, _signing_key(), algorithm=_algorithm())


def decode_token(token: str) -> dict[str, Any]:
    """Decodes and validates a JWT token."""
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[_algorithm()])
        return payload
    except JWTError as e:
        raise InvalidTokenError(details={"error": str(e)})


# AES-256-GCM Encryption for User BYOK API Keys
def get_aes_key() -> bytes:
    """Derives a fixed 32-byte key from settings.ENCRYPTION_KEY.

    SHA-256 of the raw secret yields the full 32 bytes regardless of the
    configured key length or format (never truncate/pad: raw[:32] on a
    64-hex-char key only kept 32 ASCII chars = 16 bytes of entropy).
    """
    raw = settings.ENCRYPTION_KEY.encode()
    return hashlib.sha256(raw).digest()


def encrypt_api_key(plain_key: str) -> str:
    """Encrypts an API key using AES-256-GCM and returns a base64 encoded string with nonce."""
    key = get_aes_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plain_key.encode(), None)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypts a base64 encoded AES-256-GCM ciphertext."""
    key = get_aes_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(encrypted_key.encode())
    nonce = combined[:12]
    ciphertext = combined[12:]
    decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    return decrypted_bytes.decode()
