"""AI analysis (roadmap step 8): ai_settings, ai_job, ai_report, ai_call

ai_settings  Settings > AI, append-only like the other settings: the default provider, the model of each, and
             whether a report is written by itself after an import. Version 1 holds the defaults.
ai_job       the queue the analysis service works through (one row per report asked for, by an import or
             by a tap), with its attempts and outcome.
ai_report    every report written, kept: the newest per activity is the one shown. input_sha is the hash of
             what the model was given, so the page can say when the numbers have changed since.
ai_call      one row per call to a provider, ok or not, with its tokens: the cost record.
ai_service   one row the analysis service refreshes every minute: that it is alive, and which providers
             have a key. Only that service holds the API keys; the API reads this row instead.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-13
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_settings (
            version          integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            created_at       timestamptz  NOT NULL DEFAULT now(),
            default_provider text         NOT NULL CHECK (default_provider IN ('claude', 'gemini')),
            claude_model     text         NOT NULL CHECK (length(claude_model) BETWEEN 1 AND 100),
            gemini_model     text         NOT NULL CHECK (length(gemini_model) BETWEEN 1 AND 100),
            auto_report      boolean      NOT NULL
        )
        """
    )
    op.execute(
        "INSERT INTO ai_settings (default_provider, claude_model, gemini_model, auto_report) "
        "VALUES ('claude', 'claude-sonnet-5-5', 'gemini-3.8-flash', true)"
    )
    op.execute(
        """
        CREATE TABLE ai_job (
            id           bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            kind         text         NOT NULL CHECK (kind IN ('run_report')),
            ref_id       bigint       NOT NULL,
            provider     text         CHECK (provider IN ('claude', 'gemini')),  -- null: the default at run time
            trigger      text         NOT NULL CHECK (trigger IN ('import', 'request')),
            requested_at timestamptz  NOT NULL DEFAULT now(),
            started_at   timestamptz,
            finished_at  timestamptz,
            attempts     integer      NOT NULL DEFAULT 0,
            next_try_at  timestamptz,
            ok           boolean,
            error        text
        )
        """
    )
    op.execute("CREATE INDEX ai_job_open ON ai_job (requested_at) WHERE finished_at IS NULL")
    op.execute("CREATE INDEX ai_job_ref ON ai_job (kind, ref_id, requested_at DESC)")
    op.execute(
        """
        CREATE TABLE ai_report (
            id             bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            scope          text         NOT NULL CHECK (scope IN ('activity')),
            ref_id         bigint       NOT NULL,
            created_at     timestamptz  NOT NULL DEFAULT now(),
            provider       text         NOT NULL,
            model          text         NOT NULL,
            prompt_version text         NOT NULL,
            input_sha      text         NOT NULL,
            input_tokens   integer      NOT NULL,
            output_tokens  integer      NOT NULL,
            removed        integer      NOT NULL DEFAULT 0,  -- sentences the dose guard took out
            text           text         NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ai_report_ref ON ai_report (scope, ref_id, created_at DESC)")
    op.execute(
        """
        CREATE TABLE ai_call (
            id            bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            at            timestamptz  NOT NULL DEFAULT now(),
            provider      text         NOT NULL,
            model         text         NOT NULL,
            purpose       text         NOT NULL,
            ref_id        bigint,
            ok            boolean      NOT NULL,
            input_tokens  integer,
            output_tokens integer,
            ms            integer,
            error         text
        )
        """
    )


    op.execute(
        """
        CREATE TABLE ai_service (
            id      smallint     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            seen_at timestamptz  NOT NULL,
            claude  boolean      NOT NULL,
            gemini  boolean      NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ai_service")
    op.execute("DROP TABLE ai_call")
    op.execute("DROP TABLE ai_report")
    op.execute("DROP TABLE ai_job")
    op.execute("DROP TABLE ai_settings")
