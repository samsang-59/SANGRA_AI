"""Security helpers (Layer 7, item 3): bcrypt passwords, JWTs and email tokens.

- bcrypt is slow on purpose, so it always runs in a separate thread (the event loop is never blocked).
- JWT claims: user id (sub), issue time (iat), expiry (exp, 24 hours later). Nothing else: no email, no hash.
- Email tokens: 32 random bytes from the `secrets` module; only their SHA-256 hash is stored.
"""

import asyncio
import hashlib
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

import bcrypt
import jwt

from app.core import clock

BCRYPT_MAX_BYTES = 72
JWT_ALGORITHM = "HS256"
EMAIL_TOKEN_BYTES = 32
# secrets.token_urlsafe(32) is always 43 characters (base64url without padding).
EMAIL_TOKEN_LENGTH = 43


# --- Passwords -----------------------------------------------------------------------------------

# bcrypt gets its own threads (one per CPU). On the shared default pool, a burst of logins would queue up
# every other thread job in the app (for example the Docker check of /health) behind the hashes (B2-C01).
_BCRYPT_THREADS = ThreadPoolExecutor(max_workers=os.cpu_count() or 2, thread_name_prefix="bcrypt")


async def _in_bcrypt_thread(func, *args):
    return await asyncio.get_running_loop().run_in_executor(_BCRYPT_THREADS, func, *args)


def _hash_sync(password: str, rounds: int) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds)).decode("ascii")


def _check_sync(password: str, password_hash: str) -> bool:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        # Such a password can never have been saved (the validator refuses it). Still spend the same
        # time as a real check, so the answer time does not tell anything.
        bcrypt.checkpw(b"x", password_hash.encode("ascii"))
        return False
    return bcrypt.checkpw(encoded, password_hash.encode("ascii"))


async def hash_password(password: str, rounds: int) -> str:
    return await _in_bcrypt_thread(_hash_sync, password, rounds)


async def verify_password(password: str, password_hash: str) -> bool:
    try:
        return await _in_bcrypt_thread(_check_sync, password, password_hash)
    except ValueError:  # a broken hash in the database: never a match
        return False


class DummyHash:
    """A hash to check against when the email is unknown, so "unknown email" and "wrong password"
    take the same time (B2-S01). Built once, lazily, with the configured cost."""

    def __init__(self, rounds: int):
        self._rounds = rounds
        self._hash: str | None = None

    async def get(self) -> str:
        if self._hash is None:
            self._hash = await hash_password(secrets.token_urlsafe(16), self._rounds)
        return self._hash


async def check_login_password(password: str, password_hash: str | None, dummy: DummyHash) -> bool:
    """True only for a known user with the right password. Unknown users cost the same bcrypt time."""
    if password_hash is None:
        await verify_password(password, await dummy.get())
        return False
    return await verify_password(password, password_hash)


# --- JWT -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    issued_at: int  # whole seconds (UNIX time)
    expires_at: int


class InvalidTokenError(Exception):
    pass


def create_access_token(user_id: int, secret: str, lifetime_hours: int) -> str:
    issued = int(clock.now().timestamp())
    expires = issued + int(timedelta(hours=lifetime_hours).total_seconds())
    return jwt.encode({"sub": str(user_id), "iat": issued, "exp": expires}, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str) -> TokenClaims:
    """Checks the signature (HS256 only, so "none" and other algorithms are refused) and the expiry
    (against the app clock). Raises InvalidTokenError for anything wrong."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "iat", "exp"], "verify_exp": False, "verify_iat": False},
        )
        claims = TokenClaims(int(payload["sub"]), int(payload["iat"]), int(payload["exp"]))
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidTokenError(type(exc).__name__) from None
    if claims.user_id <= 0:
        raise InvalidTokenError("bad subject")
    if clock.now().timestamp() >= claims.expires_at:
        raise InvalidTokenError("expired")
    return claims


# --- Email tokens --------------------------------------------------------------------------------

def new_email_token() -> str:
    return secrets.token_urlsafe(EMAIL_TOKEN_BYTES)


def hash_email_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
