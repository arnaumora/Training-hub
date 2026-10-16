"""insulin settings, versioned and append-only

Every save of Settings > Insulin is a new row (a new version); the newest row is the current one.
Rows can never be changed or deleted, so any past calculation can be reproduced from the version
it used. Nothing here has a default value: every field is entered by hand.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The CHECKs are sanity limits against corrupted writes, far wider than any real value.
    # The API applies the real validation, and asks for a second confirmation outside usual ranges.
    op.execute(
        """
        CREATE TABLE insulin_settings (
            version                 integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            created_at              timestamptz  NOT NULL DEFAULT now(),
            note                    text         CHECK (note IS NULL OR length(note) <= 200),
            -- [{"name": "Breakfast", "start": "07:00", "ratio": 2.5}, ...] sorted by start;
            -- each slot runs until the next one starts, wrapping past midnight.
            meal_slots              jsonb        NOT NULL CHECK (jsonb_typeof(meal_slots) = 'array'
                                                                 AND jsonb_array_length(meal_slots) BETWEEN 1 AND 8),
            -- mg/dL that 1 unit of rapid insulin lowers glucose by
            isf                     numeric(5,1) NOT NULL CHECK (isf > 0 AND isf <= 500),
            correct_above           smallint     NOT NULL CHECK (correct_above BETWEEN 40 AND 400),
            correct_to              smallint     NOT NULL CHECK (correct_to BETWEEN 40 AND 400),
            action_time_h           numeric(3,1) NOT NULL CHECK (action_time_h > 0 AND action_time_h <= 12),
            action_time_provisional boolean      NOT NULL,
            pen_increment           numeric(2,1) NOT NULL CHECK (pen_increment IN (0.5, 1.0)),
            racion_g                numeric(4,1) NOT NULL CHECK (racion_g > 0 AND racion_g <= 50),
            rapid_insulin           text         NOT NULL CHECK (length(btrim(rapid_insulin)) BETWEEN 1 AND 40),
            CHECK (correct_to <= correct_above)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION insulin_settings_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'insulin_settings is append-only: save a new version instead';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER insulin_settings_append_only BEFORE UPDATE OR DELETE ON insulin_settings "
        "FOR EACH ROW EXECUTE FUNCTION insulin_settings_is_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE insulin_settings")
    op.execute("DROP FUNCTION insulin_settings_is_append_only()")
