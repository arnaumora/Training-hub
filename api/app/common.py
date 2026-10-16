"""Helpers shared by the API modules: database connection info and the login check."""

import os

from fastapi import HTTPException, Request
from psycopg.conninfo import make_conninfo


def conninfo() -> str:
    return make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "db"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=3,
    )


def require_auth(request: Request) -> None:
    """Signed in with the password (session cookie), or a read-only API token for GET and HEAD
    (app.api_tokens: the Claude desktop connector). A token can never change anything."""
    if request.session.get("auth"):
        return
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        from . import api_tokens  # here, not at the top: api_tokens imports this module

        if not api_tokens.check(header[7:].strip()):
            raise HTTPException(status_code=401, detail="not authenticated")
        # GET only. The barcode lookup is a GET that calls Open Food Facts and stores the product: not for tokens.
        if request.method not in ("GET", "HEAD") or request.url.path.startswith("/api/barcode/"):
            raise HTTPException(status_code=403, detail="an API token can only read")
        return
    raise HTTPException(status_code=401, detail="not authenticated")
