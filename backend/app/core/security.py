import base64
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
ALGORITHM = "HS256"


def create_access_token(
    subject: str | UUID,
    role: str | None = None,
    expires_delta: timedelta | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """Creates a short-lived access token (default 15 minutes).

    Accepts an optional `role` and arbitrary `additional_claims` merged into
    the JWT payload, so callers declare identity claims through one function.
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
        "type": "access",
    }
    if role is not None:
        to_encode["role"] = role
    if additional_claims:
        to_encode.update(additional_claims)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


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
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decodes and validates a JWT token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise InvalidTokenError(details={"error": str(e)})


# AES-256-GCM Encryption for User BYOK API Keys
def get_aes_key() -> bytes:
    """Derives a fixed 32-byte key from settings.ENCRYPTION_KEY."""
    raw = settings.ENCRYPTION_KEY.encode()
    if len(raw) < 32:
        return raw.ljust(32, b"0")
    return raw[:32]


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


# CSRF Double-Submit Token
def generate_csrf_token() -> str:
    """Generates a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)
