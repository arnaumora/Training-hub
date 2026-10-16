"""doses remember the calculator suggestion they were logged with

settings_version: the Settings > Insulin version the suggestion used.
calc: the suggestion as shown (every term), so "suggested vs injected" can be compared later and any
past calculation can be checked. Both are NULL for doses logged without a suggestion.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE insulin_dose
            ADD COLUMN settings_version integer REFERENCES insulin_settings (version),
            ADD COLUMN calc jsonb CHECK (calc IS NULL OR jsonb_typeof(calc) = 'object'),
            ADD CHECK ((settings_version IS NULL) = (calc IS NULL))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE insulin_dose DROP COLUMN calc, DROP COLUMN settings_version")
