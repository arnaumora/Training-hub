"""collector_status table

One row per collector (for now only 'llu'). Lets /health tell apart "the collector cannot reach
LibreLinkUp" (a real failure) from "no new readings because LibreLink is closed on the phone"
(expected, not an outage).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE collector_status (
            name                 text        PRIMARY KEY,
            last_attempt_at      timestamptz,
            last_success_at      timestamptz,
            last_error_at        timestamptz,
            -- Short error class and message only; never credentials or response bodies.
            last_error           text,
            consecutive_failures integer     NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE collector_status")
