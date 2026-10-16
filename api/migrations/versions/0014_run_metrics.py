"""per-run numbers (roadmap step 7): run_context, run_glucose_metrics, run_training_metrics, activity_note

The three run_* tables are derived (app/derive.py, from app/run_metrics.py): one row per activity, replaced
whole on every recompute, and rebuildable at any time with `python -m app.derive --all`. Each row names the
settings versions it was computed with, so a settings change finds the rows to redo.

activity_note is not derived: it is the note you write on a run, kept when the numbers are recomputed.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-13
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_context (
            activity_id              bigint       PRIMARY KEY REFERENCES activity (id) ON DELETE CASCADE,
            computed_at              timestamptz  NOT NULL DEFAULT now(),
            insulin_settings_version integer,
            log_covered              boolean      NOT NULL,
            iob_at_start             numeric(5,2),
            min_since_last_bolus     integer,
            last_bolus_units         numeric(5,1),
            last_meal_carbs          numeric(6,1),
            min_since_last_meal      integer,
            pre_run_carbs            numeric(6,1),
            glucose_start            smallint,
            trend_start              smallint,
            h_since_basal            numeric(5,1),
            local_time               time         NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE run_glucose_metrics (
            activity_id               bigint       PRIMARY KEY REFERENCES activity (id) ON DELETE CASCADE,
            computed_at               timestamptz  NOT NULL DEFAULT now(),
            training_settings_version integer,
            glucose_start             smallint,
            glucose_end               smallint,
            nadir                     smallint,
            nadir_minute              integer,
            delta                     smallint,
            rate_per_10min            numeric(5,1),
            min_below_low             integer      NOT NULL,
            coverage_pct              numeric(4,1) NOT NULL,
            low_coverage              boolean      NOT NULL,
            readings                  integer      NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE run_training_metrics (
            activity_id               bigint       PRIMARY KEY REFERENCES activity (id) ON DELETE CASCADE,
            computed_at               timestamptz  NOT NULL DEFAULT now(),
            training_settings_version integer,
            time_in_zone              jsonb,        -- [seconds in zone 1, ..., zone 5]; null without heart rate
            hr_drift_pct              numeric(5,1),
            avg_pace_s                numeric(6,1), -- s/km over moving segments (runs, walks, hikes)
            grade_adjusted_pace_s     numeric(6,1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE activity_note (
            activity_id bigint      PRIMARY KEY REFERENCES activity (id) ON DELETE CASCADE,
            note        text        NOT NULL CHECK (length(note) BETWEEN 1 AND 2000),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE activity_note")
    op.execute("DROP TABLE run_training_metrics")
    op.execute("DROP TABLE run_glucose_metrics")
    op.execute("DROP TABLE run_context")
