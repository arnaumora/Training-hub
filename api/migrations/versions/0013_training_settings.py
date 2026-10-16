"""training settings (roadmap step 7): heart-rate zones, glucose target range, sensor lag offset

Versioned and append-only like insulin_settings: every save is a new version, so per-run metrics (time in
zone, minutes below range) can name the version they were computed with and be reproduced.

hr_zone_floors: the lowest bpm of zones 2, 3, 4 and 5 (zone 1 is everything below the first), strictly
increasing. Friel running zones from LTHR: 85, 90, 95 and 100 % of LTHR, rounded down.

Version 1 holds the starting values from the project run log (corrected 2026-10-11): LTHR 191, max 214,
resting 49, zones 162 / 171 / 181 / 191; glucose target 70-180 mg/dL; lag offset 8 min (spec default).
These are training settings, not insulin parameters: the no-defaults rule for insulin does not apply.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE training_settings (
            version        integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            created_at     timestamptz NOT NULL DEFAULT now(),
            note           text        CHECK (note IS NULL OR length(note) <= 200),
            lthr           smallint    NOT NULL CHECK (lthr BETWEEN 100 AND 230),
            max_hr         smallint    NOT NULL CHECK (max_hr BETWEEN 100 AND 240),
            resting_hr     smallint    NOT NULL CHECK (resting_hr BETWEEN 25 AND 110),
            hr_zone_floors smallint[]  NOT NULL CHECK (cardinality(hr_zone_floors) = 4),
            glucose_low    smallint    NOT NULL CHECK (glucose_low BETWEEN 50 AND 110),
            glucose_high   smallint    NOT NULL CHECK (glucose_high BETWEEN 120 AND 300),
            lag_min        smallint    NOT NULL CHECK (lag_min BETWEEN 0 AND 30),
            CHECK (resting_hr < lthr AND lthr <= max_hr AND glucose_low < glucose_high),
            CHECK (hr_zone_floors[1] < hr_zone_floors[2] AND hr_zone_floors[2] < hr_zone_floors[3]
                   AND hr_zone_floors[3] < hr_zone_floors[4] AND hr_zone_floors[4] <= max_hr)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION training_settings_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'training_settings is append-only: save a new version instead';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER training_settings_append_only BEFORE UPDATE OR DELETE ON training_settings "
        "FOR EACH ROW EXECUTE FUNCTION training_settings_is_append_only()"
    )
    op.execute(
        """
        INSERT INTO training_settings (note, lthr, max_hr, resting_hr, hr_zone_floors, glucose_low, glucose_high, lag_min)
        VALUES ('Starting values from the run log (corrected 2026-10-11): Friel zones from LTHR 191',
                191, 214, 49, '{162,171,181,191}', 70, 180, 8)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE training_settings")
    op.execute("DROP FUNCTION training_settings_is_append_only()")
