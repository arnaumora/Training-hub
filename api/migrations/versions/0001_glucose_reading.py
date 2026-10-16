"""glucose_reading table

One row per sensor minute. The live poll (one reading a minute while LibreLink is running on the
phone) and the coarse graph refetch (15-minute history) both write here; whichever arrives first
for a minute wins, and in practice that is the live reading.

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE glucose_reading (
            -- Sensor time truncated to the minute, UTC. One row per minute.
            minute      timestamptz PRIMARY KEY,
            -- Exact sensor time (LibreLinkUp factory timestamp), UTC.
            ts          timestamptz NOT NULL,
            mg_dl       smallint    NOT NULL CHECK (mg_dl > 0 AND mg_dl < 1000),
            -- LibreLinkUp trend arrow: 1 down fast ... 3 stable ... 5 up fast. NULL when unknown.
            trend       smallint    CHECK (trend BETWEEN 0 AND 5),
            source      text        NOT NULL CHECK (source IN ('llu_live', 'llu_graph', 'libreview_csv')),
            received_at timestamptz NOT NULL DEFAULT now(),
            CHECK (minute = date_trunc('minute', ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
        )
        """
    )
    op.execute("CREATE INDEX glucose_reading_source_minute ON glucose_reading (source, minute)")


def downgrade() -> None:
    op.execute("DROP TABLE glucose_reading")
