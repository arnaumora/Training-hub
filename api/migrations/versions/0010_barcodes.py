"""barcodes and Open Food Facts products (roadmap step 5)

food.source gains 'off': a packaged product fetched once from Open Food Facts by its barcode and kept
locally (source_id = the barcode, source_version = when it was fetched).

food.barcode: the GTIN a scan finds the food by (8 to 14 digits; 12-digit UPC-A is stored as 13 digits
with a leading zero). OFF products have it; an own food can have it too, when it was created after a scan.
At most one food that is not deleted holds a barcode. An own food created for a barcode that an OFF
product holds takes the barcode over (your values win); the OFF row stays for past meals.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE food
            DROP CONSTRAINT food_source_check,
            ADD CONSTRAINT food_source_check CHECK (source IN ('ciqual', 'off', 'own', 'recipe')),
            DROP CONSTRAINT food_source_id_check,
            ADD CONSTRAINT food_source_id_check
                CHECK ((source IN ('ciqual', 'off')) = (source_id IS NOT NULL AND source_version IS NOT NULL)),
            DROP CONSTRAINT food_archive_check,
            ADD CONSTRAINT food_archive_check CHECK (archived_at IS NULL OR source IN ('own', 'recipe')),
            ADD COLUMN barcode text CHECK (barcode ~ '^[0-9]{8,14}$'),
            ADD CONSTRAINT food_barcode_source_check CHECK (barcode IS NULL OR source IN ('off', 'own'))
        """
    )
    op.execute("CREATE UNIQUE INDEX food_barcode_live ON food (barcode) WHERE barcode IS NOT NULL AND archived_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX food_barcode_live")
    op.execute("DELETE FROM food WHERE source = 'off'")
    op.execute(
        """
        ALTER TABLE food
            DROP CONSTRAINT food_barcode_source_check,
            DROP COLUMN barcode,
            DROP CONSTRAINT food_archive_check,
            ADD CONSTRAINT food_archive_check CHECK (archived_at IS NULL OR source <> 'ciqual'),
            DROP CONSTRAINT food_source_id_check,
            ADD CONSTRAINT food_source_id_check
                CHECK ((source = 'ciqual') = (source_id IS NOT NULL AND source_version IS NOT NULL)),
            DROP CONSTRAINT food_source_check,
            ADD CONSTRAINT food_source_check CHECK (source IN ('ciqual', 'own', 'recipe'))
        """
    )
