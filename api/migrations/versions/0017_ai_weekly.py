"""Weekly AI summaries (roadmap step 8, change 2): reports with scope 'week' (ref_id is the Monday as
YYYYMMDD), jobs of kind 'week_summary', and the 'schedule' trigger (queued by the analysis service on Monday).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-13
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_report DROP CONSTRAINT ai_report_scope_check")
    op.execute("ALTER TABLE ai_report ADD CONSTRAINT ai_report_scope_check CHECK (scope IN ('activity', 'week'))")
    op.execute("ALTER TABLE ai_job DROP CONSTRAINT ai_job_kind_check")
    op.execute("ALTER TABLE ai_job ADD CONSTRAINT ai_job_kind_check CHECK (kind IN ('run_report', 'week_summary'))")
    op.execute("ALTER TABLE ai_job DROP CONSTRAINT ai_job_trigger_check")
    op.execute("ALTER TABLE ai_job ADD CONSTRAINT ai_job_trigger_check CHECK (trigger IN ('import', 'request', 'schedule'))")


def downgrade() -> None:
    op.execute("DELETE FROM ai_report WHERE scope = 'week'")
    op.execute("DELETE FROM ai_job WHERE kind = 'week_summary'")
    op.execute("ALTER TABLE ai_report DROP CONSTRAINT ai_report_scope_check")
    op.execute("ALTER TABLE ai_report ADD CONSTRAINT ai_report_scope_check CHECK (scope IN ('activity'))")
    op.execute("ALTER TABLE ai_job DROP CONSTRAINT ai_job_kind_check")
    op.execute("ALTER TABLE ai_job ADD CONSTRAINT ai_job_kind_check CHECK (kind IN ('run_report'))")
    op.execute("ALTER TABLE ai_job DROP CONSTRAINT ai_job_trigger_check")
    op.execute("ALTER TABLE ai_job ADD CONSTRAINT ai_job_trigger_check CHECK (trigger IN ('import', 'request'))")
