"""Planned vs done (roadmap step 9, change 2): links set by hand between planned sessions and activities.

Matching is computed when the calendar is read (app.plan_calendar), same day and same sport. A row here overrides
it for one session:
  kind 'activity'  this activity did the session (any day; the way to count a session moved to another day)
  kind 'done'      done without an activity (gym, which is not recorded on the watch)
  kind 'none'      not done: no activity is matched to it automatically
An activity does at most one session. Removing the row returns the session to automatic matching.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE plan_link (
            planned_session_id integer     PRIMARY KEY REFERENCES planned_session (id) ON DELETE CASCADE,
            kind               text        NOT NULL CHECK (kind IN ('activity', 'done', 'none')),
            activity_id        integer     UNIQUE REFERENCES activity (id) ON DELETE CASCADE,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CHECK ((kind = 'activity') = (activity_id IS NOT NULL))
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE plan_link")
