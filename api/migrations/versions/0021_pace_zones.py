"""Pace zones in Settings > Training (roadmap step 9, change 3): the targets the calendar shows on planned runs,
instead of the paces written in the .ics files (which go out of date whenever the zones are revised).

pace_zones: {"easy": [from_s, to_s], "long": ..., "steady": ..., "tempo": ..., "threshold": ..., "race": ...},
seconds per km, fastest first; a zone left out has no target. Earlier versions have none.

A new version (training_settings is append-only) carries the current values plus the pace zones from the
project run log (revised 2026-10-11, anchored on LT pace 4:55/km): easy 5:55-6:25, long run 6:00-6:30,
threshold 4:50-5:00, half marathon pace 5:15-5:30; steady 5:25-5:35 from the 11 Oct calendars. Tempo has no
value in the run log and is left empty.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-14
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE training_settings ADD COLUMN pace_zones jsonb")
    op.execute(
        """
        INSERT INTO training_settings (note, lthr, max_hr, resting_hr, hr_zone_floors, glucose_low, glucose_high, lag_min, pace_zones)
        SELECT 'Pace zones from the run log (revised 2026-10-11)', lthr, max_hr, resting_hr, hr_zone_floors,
               glucose_low, glucose_high, lag_min,
               '{"easy": [355, 385], "long": [360, 390], "steady": [325, 335], "threshold": [290, 300], "race": [315, 330]}'::jsonb
        FROM training_settings ORDER BY version DESC LIMIT 1
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE training_settings DISABLE TRIGGER training_settings_append_only")
    op.execute("DELETE FROM training_settings WHERE pace_zones IS NOT NULL")
    op.execute("ALTER TABLE training_settings ENABLE TRIGGER training_settings_append_only")
    op.execute("ALTER TABLE training_settings DROP COLUMN pace_zones")
