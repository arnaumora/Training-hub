"""meals built from foods (roadmap step 5)

A carb entry can now be a meal: carb_intake.entry = 'meal' and meal_id points at the meal. Each item keeps
the food's name and values per 100 g as they were when logged, so later changes to the food table never
change a past meal. carb_intake.grams holds the meal's total available carbohydrate (it can be 0, for a meal
of meat or fish, so its kcal and protein are still logged).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-12
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE meal (
            id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE meal_item (
            id        bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            meal_id   bigint       NOT NULL REFERENCES meal (id),
            position  smallint     NOT NULL CHECK (position >= 0),
            food_id   bigint       NOT NULL REFERENCES food (id),
            food_name text         NOT NULL,
            grams     numeric(6,1) NOT NULL CHECK (grams > 0 AND grams <= 5000),
            -- the food's values per 100 g when the meal was logged (NULL: not given by the source)
            kcal      numeric(6,1),
            carbs     numeric(5,2) NOT NULL,
            sugars    numeric(5,2),
            fibre     numeric(5,2),
            protein   numeric(5,2),
            fat       numeric(5,2),
            salt      numeric(5,2),
            UNIQUE (meal_id, position)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE carb_intake
            ADD COLUMN meal_id bigint REFERENCES meal (id),
            DROP CONSTRAINT carb_intake_grams_check,
            DROP CONSTRAINT carb_intake_entry_check,
            ADD CONSTRAINT carb_intake_grams_check
                CHECK (grams <= 1000 AND (grams > 0 OR (entry = 'meal' AND grams = 0))),
            ADD CONSTRAINT carb_intake_entry_check CHECK (entry IN ('grams', 'raciones', 'meal')),
            ADD CONSTRAINT carb_intake_meal_check CHECK ((entry = 'meal') = (meal_id IS NOT NULL))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE carb_intake
            DROP CONSTRAINT carb_intake_meal_check,
            DROP CONSTRAINT carb_intake_entry_check,
            DROP CONSTRAINT carb_intake_grams_check,
            DROP COLUMN meal_id,
            ADD CONSTRAINT carb_intake_entry_check CHECK (entry IN ('grams', 'raciones')),
            ADD CONSTRAINT carb_intake_grams_check CHECK (grams > 0 AND grams <= 1000)
        """
    )
    op.execute("DROP TABLE meal_item")
    op.execute("DROP TABLE meal")
