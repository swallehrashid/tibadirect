"""indexes for the lists and sweeps that read tables which only grow

Found by loading twenty thousand cases, fifty thousand donations and a quarter of a million audit
entries into a scratch database and reading the plans: the audit log and case list sort by time,
the review queue filters by status and sorts by time, and the payout poll and the webhook sweep
look at a small open part of tables that grow without end, the hospital's queue asks which bills
have no confirmation yet and which wait for a second approval, booking a payment checks the
ledger by what it is for, and the hourly sweep that deletes original files reads only the ones
still held. The partial indexes hold only the open part.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26

"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_cases_created_at", "cases", ["created_at"])
    op.create_index("ix_review_items_status_created_at", "review_items", ["status", "created_at"])
    op.create_index(
        "ix_settlements_open",
        "settlements",
        ["status"],
        postgresql_where="status IN ('pending', 'processing')",
    )
    op.create_index("ix_confirmations_document_id", "confirmations", ["document_id"])
    op.create_index("ix_confirmations_hospital_status", "confirmations", ["hospital_id", "status"])
    op.create_index("ix_ledger_entries_ref", "ledger_entries", ["ref_type", "ref_id"])
    op.create_index(
        "ix_case_documents_raw_held",
        "case_documents",
        ["case_id"],
        postgresql_where="storage_key IS NOT NULL AND raw_deleted_at IS NULL",
    )
    op.create_index(
        "ix_webhook_events_unprocessed",
        "webhook_events",
        ["received_at"],
        postgresql_where="status = 'received'",
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_unprocessed", table_name="webhook_events")
    op.drop_index("ix_case_documents_raw_held", table_name="case_documents")
    op.drop_index("ix_ledger_entries_ref", table_name="ledger_entries")
    op.drop_index("ix_confirmations_hospital_status", table_name="confirmations")
    op.drop_index("ix_confirmations_document_id", table_name="confirmations")
    op.drop_index("ix_settlements_open", table_name="settlements")
    op.drop_index("ix_review_items_status_created_at", table_name="review_items")
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
