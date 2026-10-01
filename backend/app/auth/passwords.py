"""argon2id password hashing — one hasher instance, constant-time verify.

Why argon2id over bcrypt: GPU-resistant, current OWASP recommendation.
Params match the ones baked into migration 0008's seed so a verify of the
seeded hash never falls into argon2's "rehash recommended" path.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Single process-wide hasher. argon2id is argon2-cffi's default `type`.
_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)


def hash_password(password: str) -> str:
    """Return a self-describing argon2id hash string."""
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verify; returns False for any mismatch or malformed hash."""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, Exception):  # noqa: BLE001 — any argon2 error = fail closed
        return False
