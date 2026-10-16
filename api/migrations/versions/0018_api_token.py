"""API tokens (roadmap step 8, change 3): read-only access for the Claude desktop connector.

A token is shown once when it is made (python -m app.api_tokens create NAME) and only its SHA-256 is kept.
A request with "Authorization: Bearer <token>" counts as signed in for reading: GET only. Anything that
changes data still needs the password session.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-13
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_token (
            id           integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name         text         NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            token_sha256 text         NOT NULL UNIQUE,
            created_at   timestamptz  NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            revoked_at   timestamptz
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE api_token")
