"""own names for foods (roadmap step 5)

own_name: a short personal name for a food ("macarrones"), shown instead of the source name and searched
first. own_search is its accent-free search form. Importers never touch either column, so a re-import of
CIQUAL keeps every own name.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE food
            ADD COLUMN own_name text CHECK (own_name IS NULL OR length(btrim(own_name)) BETWEEN 1 AND 60),
            ADD COLUMN own_search text,
            ADD CONSTRAINT food_own_search_check CHECK ((own_name IS NULL) = (own_search IS NULL))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE food DROP CONSTRAINT food_own_search_check, DROP COLUMN own_search, DROP COLUMN own_name")
