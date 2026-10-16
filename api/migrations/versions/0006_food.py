"""food: reference foods with values per 100 g (roadmap step 5)

Filled by importers (CIQUAL first; Open Food Facts and own foods come in later changes of step 5).
Carbohydrate follows the EU convention: available carbohydrate, fibre excluded (fibre is its own column).
A value is NULL when the source does not give it; 0 when the source says zero or trace.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE food (
            id             bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source         text         NOT NULL CHECK (source IN ('ciqual')),
            source_id      text         NOT NULL,
            source_version text         NOT NULL,
            name_en        text,
            name_fr        text,
            group_en       text,
            group_fr       text,
            -- lower case, accents removed, both names: what search matches against
            search_text    text         NOT NULL,
            kcal           numeric(6,1) CHECK (kcal >= 0),
            carbs          numeric(5,2) CHECK (carbs BETWEEN 0 AND 100),
            sugars         numeric(5,2) CHECK (sugars BETWEEN 0 AND 100),
            fibre          numeric(5,2) CHECK (fibre BETWEEN 0 AND 100),
            protein        numeric(5,2) CHECK (protein BETWEEN 0 AND 100),
            fat            numeric(5,2) CHECK (fat BETWEEN 0 AND 100),
            salt           numeric(5,2) CHECK (salt BETWEEN 0 AND 100),
            imported_at    timestamptz  NOT NULL DEFAULT now(),
            UNIQUE (source, source_id),
            CHECK (name_en IS NOT NULL OR name_fr IS NOT NULL)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE food")
