"""AI reports default to Gemini (decided 2026-10-13): reports run on the Gemini API; Claude is used through the
Claude desktop app and its connector (step 8, change 3), and its API key is optional.

Adds a Settings > AI version with Gemini as the default, only while the settings are still the untouched
seed (version 1, Claude): a choice already saved by hand is left alone.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-13
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO ai_settings (default_provider, claude_model, gemini_model, auto_report)
        SELECT 'gemini', claude_model, gemini_model, auto_report FROM ai_settings
        WHERE version = 1 AND default_provider = 'claude' AND (SELECT count(*) FROM ai_settings) = 1
        """
    )


def downgrade() -> None:
    pass  # settings are append-only; the version stays in the history
