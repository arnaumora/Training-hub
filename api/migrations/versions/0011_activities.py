"""activities from FIT files (roadmap step 6)

One activity per FIT session: a normal run is one session; a multisport file (a brick, a triathlon) has one
session per sport and so gives one activity per leg. A FIT file is identified by its file_id message
(manufacturer, serial number, time created) or, when that is missing, by its SHA-256; the same file arriving
twice (manual upload and Garmin sync) is imported once.

All three tables are derived from the raw FIT file in ./data/raw/fit/ (fit_path) and can be
rebuilt from it: re-importing a file replaces its rows.

activity_record keeps one row per second as recorded (1 s on the watch); lat/lon in degrees.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE activity (
            id                 bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            fit_key            text         NOT NULL,
            session_index      smallint     NOT NULL CHECK (session_index >= 0),
            source             text         NOT NULL CHECK (source IN ('upload', 'garmin')),
            garmin_activity_id bigint,
            sport              text         NOT NULL,
            sub_sport          text,
            start_ts           timestamptz  NOT NULL,
            end_ts             timestamptz  NOT NULL CHECK (end_ts >= start_ts),
            elapsed_s          numeric(8,1) NOT NULL CHECK (elapsed_s >= 0),
            moving_s           numeric(8,1) CHECK (moving_s >= 0),
            distance_m         numeric(9,1) CHECK (distance_m >= 0),
            avg_hr             smallint     CHECK (avg_hr BETWEEN 1 AND 255),
            max_hr             smallint     CHECK (max_hr BETWEEN 1 AND 255),
            ascent_m           numeric(6,1),
            descent_m          numeric(6,1),
            calories           integer,
            avg_speed_mps      numeric(6,3),
            device             text,
            fit_path           text         NOT NULL,
            fit_sha256         text         NOT NULL,
            parser_version     text         NOT NULL,
            imported_at        timestamptz  NOT NULL DEFAULT now(),
            UNIQUE (fit_key, session_index)
        )
        """
    )
    op.execute("CREATE INDEX activity_start ON activity (start_ts DESC)")
    op.execute("CREATE INDEX activity_garmin ON activity (garmin_activity_id) WHERE garmin_activity_id IS NOT NULL")
    op.execute(
        """
        CREATE TABLE activity_lap (
            activity_id   bigint       NOT NULL REFERENCES activity (id) ON DELETE CASCADE,
            idx           smallint     NOT NULL CHECK (idx >= 0),
            start_ts      timestamptz  NOT NULL,
            elapsed_s     numeric(8,1),
            moving_s      numeric(8,1),
            distance_m    numeric(9,1),
            avg_hr        smallint,
            max_hr        smallint,
            avg_speed_mps numeric(6,3),
            PRIMARY KEY (activity_id, idx)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE activity_record (
            activity_id bigint       NOT NULL REFERENCES activity (id) ON DELETE CASCADE,
            ts          timestamptz  NOT NULL,
            elapsed_s   integer      NOT NULL,
            distance_m  numeric(9,1),
            hr          smallint,
            speed_mps   numeric(6,3),
            altitude_m  numeric(6,1),
            cadence     smallint,
            lat         double precision,
            lon         double precision,
            PRIMARY KEY (activity_id, ts)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE activity_record")
    op.execute("DROP TABLE activity_lap")
    op.execute("DROP TABLE activity")
