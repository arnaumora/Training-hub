"""Read-only API tokens (roadmap step 8, change 3), for the Claude desktop connector.

  python -m app.api_tokens create "claude desktop"   makes a token and prints it once
  python -m app.api_tokens list                      every token: id, name, made, last used, revoked
  python -m app.api_tokens revoke ID                 stops a token at once

Only the token's SHA-256 is stored. app.common.require_auth accepts "Authorization: Bearer <token>" for GET
and HEAD requests only: a token can read everything the app shows and change nothing.
"""

import hashlib
import secrets
import sys

import psycopg
from psycopg.rows import dict_row

from .common import conninfo

PREFIX = "th_"  # recognisable in a config file
LAST_USED_EVERY = "1 minute"  # last_used_at is refreshed at most this often


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check(token: str) -> bool:
    """True for a live token; notes when it was used (at most once a minute)."""
    if not token.startswith(PREFIX) or len(token) > 200:
        return False
    with psycopg.connect(conninfo(), autocommit=True) as conn:
        r = conn.execute(
            f"""UPDATE api_token SET last_used_at = CASE
                    WHEN last_used_at IS NULL OR last_used_at < now() - interval '{LAST_USED_EVERY}' THEN now()
                    ELSE last_used_at END
                WHERE token_sha256 = %s AND revoked_at IS NULL RETURNING id""",
            (digest(token),),
        ).fetchone()
    return r is not None


def create(conn, name: str) -> str:
    token = PREFIX + secrets.token_urlsafe(32)
    conn.execute("INSERT INTO api_token (name, token_sha256) VALUES (%s, %s)", (name, digest(token)))
    return token


def main(argv: list[str]) -> int:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if len(argv) == 2 and argv[0] == "create" and 1 <= len(argv[1]) <= 60:
            token = create(conn, argv[1])
            conn.commit()
            print("Read-only token (shown once; keep it like a password):")
            print(token)
            return 0
        if argv == ["list"]:
            for r in conn.execute("SELECT id, name, created_at, last_used_at, revoked_at FROM api_token ORDER BY id").fetchall():
                state = f"revoked {r['revoked_at']:%Y-%m-%d}" if r["revoked_at"] else "live"
                used = f"{r['last_used_at']:%Y-%m-%d %H:%M} UTC" if r["last_used_at"] else "never"
                print(f"{r['id']:>3}  {r['name']:<24} made {r['created_at']:%Y-%m-%d}, last used {used}, {state}")
            return 0
        if len(argv) == 2 and argv[0] == "revoke" and argv[1].isdigit():
            n = conn.execute("UPDATE api_token SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (int(argv[1]),)).rowcount
            conn.commit()
            print("revoked" if n else "no live token with that id")
            return 0 if n else 1
    print('usage: python -m app.api_tokens create NAME | list | revoke ID')
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
