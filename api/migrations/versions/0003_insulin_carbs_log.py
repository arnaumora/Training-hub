"""insulin and carbs log, with an append-only audit trail

insulin_dose and carb_intake are written by hand from the phone. Deletes are soft (deleted_at),
and a trigger copies every edit and delete into log_audit, which itself cannot be changed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE insulin_dose (
            id           bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ts           timestamptz  NOT NULL,
            -- Sanity bounds against typos only; not a dosing rule.
            units        numeric(4,1) NOT NULL CHECK (units > 0 AND units <= 100),
            kind         text         NOT NULL CHECK (kind IN ('rapid', 'basal')),
            insulin_name text         NOT NULL CHECK (length(btrim(insulin_name)) BETWEEN 1 AND 40),
            note         text         CHECK (note IS NULL OR length(note) <= 500),
            created_at   timestamptz  NOT NULL DEFAULT now(),
            updated_at   timestamptz  NOT NULL DEFAULT now(),
            deleted_at   timestamptz
        )
        """
    )
    op.execute("CREATE INDEX insulin_dose_ts ON insulin_dose (ts) WHERE deleted_at IS NULL")

    op.execute(
        """
        CREATE TABLE carb_intake (
            id          bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ts          timestamptz  NOT NULL,
            grams       numeric(5,1) NOT NULL CHECK (grams > 0 AND grams <= 1000),
            -- How the amount was typed. Step 3b adds 'meal'.
            entry       text         NOT NULL CHECK (entry IN ('grams', 'raciones')),
            -- Racion size used for a raciones entry, so the conversion stays reproducible.
            racion_g    numeric(4,1) CHECK (racion_g > 0),
            description text         CHECK (description IS NULL OR length(description) <= 200),
            created_at  timestamptz  NOT NULL DEFAULT now(),
            updated_at  timestamptz  NOT NULL DEFAULT now(),
            deleted_at  timestamptz,
            CHECK ((entry = 'raciones') = (racion_g IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX carb_intake_ts ON carb_intake (ts) WHERE deleted_at IS NULL")

    op.execute(
        """
        CREATE TABLE log_audit (
            id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            table_name text        NOT NULL,
            row_id     bigint      NOT NULL,
            action     text        NOT NULL CHECK (action IN ('update', 'delete')),
            old_row    jsonb       NOT NULL,
            new_row    jsonb,
            at         timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX log_audit_row ON log_audit (table_name, row_id)")

    op.execute(
        """
        CREATE FUNCTION log_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END
        $$
        """
    )
    # A soft delete (deleted_at going from NULL to a time) is recorded as 'delete';
    # a hard DELETE from SQL is recorded too, with the whole old row.
    op.execute(
        """
        CREATE FUNCTION log_write_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                INSERT INTO log_audit (table_name, row_id, action, old_row, new_row)
                VALUES (TG_TABLE_NAME, OLD.id, 'delete', to_jsonb(OLD), NULL);
                RETURN OLD;
            END IF;
            INSERT INTO log_audit (table_name, row_id, action, old_row, new_row)
            VALUES (
                TG_TABLE_NAME, OLD.id,
                CASE WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN 'delete' ELSE 'update' END,
                to_jsonb(OLD), to_jsonb(NEW)
            );
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION log_audit_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'log_audit is append-only';
        END
        $$
        """
    )
    for table in ("insulin_dose", "carb_intake"):
        op.execute(
            f"CREATE TRIGGER {table}_touch BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION log_touch_updated_at()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_audit AFTER UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION log_write_audit()"
        )
    op.execute(
        "CREATE TRIGGER log_audit_append_only BEFORE UPDATE OR DELETE ON log_audit "
        "FOR EACH ROW EXECUTE FUNCTION log_audit_is_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE log_audit")
    op.execute("DROP TABLE carb_intake")
    op.execute("DROP TABLE insulin_dose")
    op.execute("DROP FUNCTION log_audit_is_append_only()")
    op.execute("DROP FUNCTION log_write_audit()")
    op.execute("DROP FUNCTION log_touch_updated_at()")
