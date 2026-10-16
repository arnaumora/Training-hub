"""own foods and recipes (roadmap step 5)

food.source gains:
  own     a food entered by hand with its values per 100 g (for example from a label)
  recipe  values per 100 g computed by the API from its ingredients (recipe_item) and batch_g
Both have no source_id or source_version; their name is in name_en and search_text.

batch_g: a recipe's weight when done (the whole pot, weighed after cooking). NULL means the sum of the
ingredient weights. archived_at hides an own food or recipe from search and lists; past meals keep
pointing at it (meal_item copies the values anyway), so it is never deleted.

A recipe's ingredients are never recipes themselves (checked by the API), so recomputing is one level deep.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE food
            DROP CONSTRAINT food_source_check,
            ADD CONSTRAINT food_source_check CHECK (source IN ('ciqual', 'own', 'recipe')),
            ALTER COLUMN source_id DROP NOT NULL,
            ALTER COLUMN source_version DROP NOT NULL,
            ADD CONSTRAINT food_source_id_check
                CHECK ((source = 'ciqual') = (source_id IS NOT NULL AND source_version IS NOT NULL)),
            ADD COLUMN batch_g numeric(7,1) CHECK (batch_g > 0 AND batch_g <= 50000),
            ADD CONSTRAINT food_batch_check CHECK (batch_g IS NULL OR source = 'recipe'),
            ADD COLUMN archived_at timestamptz,
            ADD CONSTRAINT food_archive_check CHECK (archived_at IS NULL OR source <> 'ciqual')
        """
    )
    op.execute(
        """
        CREATE TABLE recipe_item (
            id        bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            recipe_id bigint       NOT NULL REFERENCES food (id),
            position  smallint     NOT NULL CHECK (position >= 0),
            food_id   bigint       NOT NULL REFERENCES food (id),
            grams     numeric(7,1) NOT NULL CHECK (grams > 0 AND grams <= 20000),
            UNIQUE (recipe_id, position)
        )
        """
    )
    op.execute("CREATE INDEX recipe_item_food ON recipe_item (food_id)")


def downgrade() -> None:
    op.execute("DROP TABLE recipe_item")
    op.execute("DELETE FROM food WHERE source <> 'ciqual'")
    op.execute(
        """
        ALTER TABLE food
            DROP CONSTRAINT food_archive_check,
            DROP COLUMN archived_at,
            DROP CONSTRAINT food_batch_check,
            DROP COLUMN batch_g,
            DROP CONSTRAINT food_source_id_check,
            ALTER COLUMN source_version SET NOT NULL,
            ALTER COLUMN source_id SET NOT NULL,
            DROP CONSTRAINT food_source_check,
            ADD CONSTRAINT food_source_check CHECK (source IN ('ciqual'))
        """
    )
