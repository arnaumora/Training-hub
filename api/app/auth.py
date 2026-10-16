"""Single-user login.

The password hash lives in a file mounted as a Docker secret, never in git or in .env.
Sessions are signed cookies (Starlette SessionMiddleware). The cookie only says
"authenticated"; rotating SESSION_SECRET logs every session out.
"""

import os
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

HASH_FILE = os.environ.get("APP_PASSWORD_HASH_FILE", "/run/secrets/app_password_hash")

_hasher = PasswordHasher()


def load_password_hash() -> str:
    """Read the argon2 hash. Raises at startup if it is missing, so the app never runs without a password."""
    try:
        with open(HASH_FILE, encoding="ascii") as f:
            value = f.read().strip()
    except FileNotFoundError as e:
        raise RuntimeError(f"password hash file not found: {HASH_FILE}") from e
    if not value.startswith("$argon2"):
        raise RuntimeError(f"password hash file does not contain an argon2 hash: {HASH_FILE}")
    return value


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_password(password: str) -> str:
    return _hasher.hash(password)


class LoginThrottle:
    """After MAX_FAILURES wrong passwords within WINDOW seconds, refuse all logins for LOCK seconds.

    In memory only: a container restart clears it. Enough for one user behind Tailscale.
    """

    MAX_FAILURES = 5
    WINDOW = 15 * 60
    LOCK = 15 * 60

    def __init__(self) -> None:
        self._failures: deque[float] = deque()
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def locked(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return now < self._locked_until

    def record_failure(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._failures.append(now)
            while self._failures and self._failures[0] < now - self.WINDOW:
                self._failures.popleft()
            if len(self._failures) >= self.MAX_FAILURES:
                self._locked_until = now + self.LOCK
                self._failures.clear()

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
