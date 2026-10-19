"""initial schema

The first migration, generated from the models and then reviewed and edited by hand:

- the ``citext`` extension is created first (case-insensitive email columns);
- every native enum is created explicitly, once, before any table that uses it, and dropped on
  downgrade; the values are written out here so this file never changes when a Python enum does;
- the circular reference between ``cases`` and ``case_documents`` (the case photo) is added after
  both tables exist;
- the ledger gets its append-only trigger.

Revision ID: 0001
Revises:
Create Date: 2026-10-19

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ENUMS: dict[str, tuple[str, ...]] = {
    "actor_type": ("user", "system", "agent", "paypal"),
    "agent_action_status": ("proposed", "approved", "rejected", "executed", "failed"),
    "agent_action_type": ("refund", "payout", "freeze"),
    "appeal_input_kind": ("text", "image"),
    "case_status": (
        "draft",
        "pending_triage",
        "in_review",
        "pending_confirmation",
        "live",
        "funded",
        "closed",
        "cancelled",
        "frozen",
    ),
    "close_reason": (
        "fully_settled",
        "final_bill_lower",
        "treatment_cancelled",
        "patient_deceased",
        "admin_cancelled",
        "fraud",
    ),
    "confirmation_request_status": ("sent", "verified", "expired", "locked"),
    "confirmation_status": ("awaiting_second_approval", "confirmed", "queried", "rejected"),
    "consent_actor": ("self", "guardian", "administrator"),
    "consent_scope": (
        "hospital_verification",
        "public_page",
        "ai_processing_abroad",
        "receipt_sharing",
    ),
    "document_kind": ("invoice", "estimate", "final_bill", "photo"),
    "document_status": ("uploaded", "processing", "triaged", "failed"),
    "donation_status": (
        "created",
        "captured",
        "partially_refunded",
        "refunded",
        "reversed",
        "denied",
        "not_captured_case_closed",
    ),
    "excess_choice": ("refund", "solidarity_pool", "follow_up_care"),
    "ledger_account": (
        "case_funds",
        "paypal_fees",
        "hospital_settled",
        "donor_refunds",
        "solidarity_pool",
    ),
    "ledger_entry_type": (
        "donation_captured",
        "paypal_fee",
        "settlement_sent",
        "refund_issued",
        "pool_transfer",
        "reversal",
        "adjustment",
    ),
    "otp_channel": ("email", "sms"),
    "refund_reason": ("excess_policy", "freeze", "cancellation", "admin"),
    "refund_status": ("pending", "succeeded", "failed"),
    "review_resolution": (
        "approved_for_confirmation",
        "kept_unverified",
        "case_cancelled",
        "no_action",
    ),
    "review_source": ("ai_flag", "family_request", "insider_anomaly", "admin"),
    "review_status": ("open", "resolved"),
    "risk_band": ("low", "medium", "high"),
    "settlement_method": ("paypal_payouts", "mock_partner", "jenga"),
    "settlement_status": ("pending", "processing", "succeeded", "failed"),
    "staff_role": ("billing_clerk", "billing_supervisor"),
    "triage_route": ("hospital_confirmation", "manual_review"),
    "trust_level": ("unverified", "pending_confirmation", "hospital_confirmed"),
    "user_role": ("family", "hospital_staff", "admin"),
    "webhook_status": ("received", "processed", "ignored", "failed"),
}


def _enum(name: str) -> postgresql.ENUM:
    """Reference an enum type that ``_create_enums`` has already created."""
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _create_enums() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=False)


def _drop_enums() -> None:
    bind = op.get_bind()
    for name, values in reversed(list(ENUMS.items())):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    _create_enums()

    op.create_table(
        "facility_register_entries",
        sa.Column("register_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("county", sa.String(length=40), nullable=False),
        sa.Column("facility_level", sa.String(length=20), nullable=True),
        sa.Column("ownership", sa.String(length=20), nullable=True),
        sa.Column("official_email", sa.String(length=160), nullable=False),
        sa.Column("official_phone", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facility_register_entries")),
        sa.UniqueConstraint(
            "register_code", name=op.f("uq_facility_register_entries_register_code")
        ),
    )
    op.create_index(
        op.f("ix_facility_register_entries_normalized_name"),
        "facility_register_entries",
        ["normalized_name"],
        unique=False,
    )

    op.create_table(
        "fx_rates",
        sa.Column("base", sa.CHAR(length=3), nullable=False),
        sa.Column("quote", sa.CHAR(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rate > 0", name=op.f("ck_fx_rates_rate_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rates")),
    )
    op.create_index(
        "ix_fx_rates_pair_fetched_at",
        "fx_rates",
        ["base", "quote", sa.literal_column("fetched_at DESC")],
        unique=False,
    )

    op.create_table(
        "login_codes",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("code_hmac", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hmac", sa.CHAR(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 10", name=op.f("ck_login_codes_attempts_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_codes")),
    )
    op.create_index(op.f("ix_login_codes_email"), "login_codes", ["email"], unique=False)

    op.create_table(
        "users",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("role", _enum("user_role"), nullable=False),
        sa.Column("preferred_language", sa.String(length=2), server_default="en", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "preferred_language IN ('en', 'sw')", name=op.f("ck_users_preferred_language_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)

    op.create_table(
        "webhook_events",
        sa.Column("paypal_event_id", sa.String(length=60), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=True),
        sa.Column("resource_id", sa.String(length=60), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("status", _enum("webhook_status"), server_default="received", nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint("paypal_event_id", name=op.f("uq_webhook_events_paypal_event_id")),
    )
    op.create_index(
        op.f("ix_webhook_events_event_type"), "webhook_events", ["event_type"], unique=False
    )
    op.create_index(
        op.f("ix_webhook_events_resource_id"), "webhook_events", ["resource_id"], unique=False
    )

    op.create_table(
        "audit_log",
        sa.Column("actor_type", _enum("actor_type"), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("ip_hmac", sa.CHAR(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"], unique=False)

    op.create_table(
        "hospitals",
        sa.Column("register_entry_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("county", sa.String(length=40), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "settlement_method",
            _enum("settlement_method"),
            server_default="paypal_payouts",
            nullable=False,
        ),
        sa.Column("paypal_payout_email", sa.String(length=160), nullable=True),
        sa.Column("settlement_account_ref_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "two_person_threshold_kes_minor",
            sa.BigInteger(),
            server_default="20000000",
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "two_person_threshold_kes_minor >= 0",
            name=op.f("ck_hospitals_two_person_threshold_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["register_entry_id"],
            ["facility_register_entries.id"],
            name=op.f("fk_hospitals_register_entry_id_facility_register_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hospitals")),
        sa.UniqueConstraint("register_entry_id", name=op.f("uq_hospitals_register_entry_id")),
    )
    op.create_table(
        "cases",
        sa.Column("public_slug", sa.String(length=12), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("hospital_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=40), nullable=False),
        sa.Column("region", sa.String(length=40), nullable=False),
        sa.Column("story", sa.String(length=600), nullable=True),
        sa.Column("patient_ref_enc", sa.LargeBinary(), nullable=False),
        sa.Column("patient_ref_hmac", sa.CHAR(length=64), nullable=False),
        sa.Column("is_minor", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("show_photo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("photo_document_id", sa.UUID(), nullable=True),
        sa.Column("status", _enum("case_status"), server_default="draft", nullable=False),
        sa.Column("trust_level", _enum("trust_level"), server_default="unverified", nullable=False),
        sa.Column("target_kes_minor", sa.BigInteger(), nullable=True),
        sa.Column("is_estimate", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("buffer_bps", sa.Integer(), server_default="500", nullable=False),
        sa.Column("usd_goal_minor", sa.BigInteger(), nullable=True),
        sa.Column("pledged_kes_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("settled_kes_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("captured_net_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("settled_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("refunded_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("donor_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", _enum("close_reason"), nullable=True),
        sa.Column("freeze_reason", sa.String(length=300), nullable=True),
        sa.Column("follow_up_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'closed' OR close_reason IS NOT NULL",
            name=op.f("ck_cases_closed_has_reason"),
        ),
        sa.CheckConstraint(
            "status <> 'frozen' OR freeze_reason IS NOT NULL",
            name=op.f("ck_cases_frozen_has_reason"),
        ),
        sa.CheckConstraint("buffer_bps BETWEEN 0 AND 2000", name=op.f("ck_cases_buffer_range")),
        sa.CheckConstraint(
            "captured_net_usd_minor >= 0", name=op.f("ck_cases_captured_net_non_negative")
        ),
        sa.CheckConstraint("pledged_kes_minor >= 0", name=op.f("ck_cases_pledged_non_negative")),
        sa.CheckConstraint("refunded_usd_minor >= 0", name=op.f("ck_cases_refunded_non_negative")),
        sa.CheckConstraint("settled_kes_minor >= 0", name=op.f("ck_cases_settled_non_negative")),
        sa.CheckConstraint(
            "settled_usd_minor >= 0", name=op.f("ck_cases_settled_usd_non_negative")
        ),
        sa.CheckConstraint("target_kes_minor >= 0", name=op.f("ck_cases_target_non_negative")),
        sa.CheckConstraint("usd_goal_minor >= 0", name=op.f("ck_cases_usd_goal_non_negative")),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_cases_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"],
            ["hospitals.id"],
            name=op.f("fk_cases_hospital_id_hospitals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
        sa.UniqueConstraint("public_slug", name=op.f("uq_cases_public_slug")),
    )
    op.create_index(
        "ix_cases_active_patient_ref",
        "cases",
        ["patient_ref_hmac"],
        unique=False,
        postgresql_where=sa.text("status NOT IN ('closed','cancelled')"),
    )
    op.create_index(op.f("ix_cases_created_by_id"), "cases", ["created_by_id"], unique=False)
    op.create_index(op.f("ix_cases_hospital_id"), "cases", ["hospital_id"], unique=False)
    op.create_index("ix_cases_hospital_status", "cases", ["hospital_id", "status"], unique=False)
    op.create_index(op.f("ix_cases_status"), "cases", ["status"], unique=False)

    op.create_table(
        "hospital_staff",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("hospital_id", sa.UUID(), nullable=False),
        sa.Column("staff_role", _enum("staff_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"],
            ["hospitals.id"],
            name=op.f("fk_hospital_staff_hospital_id_hospitals"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_hospital_staff_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hospital_staff")),
        sa.UniqueConstraint("user_id", name=op.f("uq_hospital_staff_user_id")),
    )
    op.create_index(
        op.f("ix_hospital_staff_hospital_id"), "hospital_staff", ["hospital_id"], unique=False
    )

    op.create_table(
        "agent_actions",
        sa.Column("action_type", _enum("agent_action_type"), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "status", _enum("agent_action_status"), server_default="proposed", nullable=False
        ),
        sa.Column("decided_by_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status = 'proposed' OR (decided_by_id IS NOT NULL AND decided_at IS NOT NULL)",
            name=op.f("ck_agent_actions_decided_by_a_human"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_agent_actions_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_agent_actions_decided_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_actions")),
    )
    op.create_index(op.f("ix_agent_actions_case_id"), "agent_actions", ["case_id"], unique=False)

    op.create_table(
        "appeal_checks",
        sa.Column("input_hmac", sa.CHAR(length=64), nullable=False),
        sa.Column("input_kind", _enum("appeal_input_kind"), nullable=False),
        sa.Column("risk_score", sa.SmallInteger(), nullable=False),
        sa.Column("risk_band", _enum("risk_band"), nullable=False),
        sa.Column(
            "reasons_en", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "reasons_sw", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("matched_case_id", sa.UUID(), nullable=True),
        sa.Column("used_llm", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "risk_score BETWEEN 0 AND 100", name=op.f("ck_appeal_checks_risk_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["matched_case_id"],
            ["cases.id"],
            name=op.f("fk_appeal_checks_matched_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appeal_checks")),
    )
    op.create_index(
        op.f("ix_appeal_checks_input_hmac"), "appeal_checks", ["input_hmac"], unique=False
    )

    op.create_table(
        "case_documents",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("uploaded_by_id", sa.UUID(), nullable=False),
        sa.Column("kind", _enum("document_kind"), nullable=False),
        sa.Column("status", _enum("document_status"), server_default="uploaded", nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=60), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.SmallInteger(), nullable=True),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("phash", sa.CHAR(length=16), nullable=True),
        sa.Column("fingerprint", sa.CHAR(length=64), nullable=True),
        sa.Column("redacted_text", sa.Text(), nullable=True),
        sa.Column("raw_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 10485760", name=op.f("ck_case_documents_size_range")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_documents_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name=op.f("fk_case_documents_uploaded_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_documents")),
    )
    op.create_index(op.f("ix_case_documents_case_id"), "case_documents", ["case_id"], unique=False)
    op.create_index(
        op.f("ix_case_documents_fingerprint"), "case_documents", ["fingerprint"], unique=False
    )
    op.create_index(op.f("ix_case_documents_phash"), "case_documents", ["phash"], unique=False)
    op.create_index(op.f("ix_case_documents_sha256"), "case_documents", ["sha256"], unique=False)

    # cases and case_documents reference each other, so this key is added once both exist.
    op.create_foreign_key(
        op.f("fk_cases_photo_document_id_case_documents"),
        "cases",
        "case_documents",
        ["photo_document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "confirmation_requests",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("hospital_id", sa.UUID(), nullable=False),
        sa.Column("requested_by_staff_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("channel", _enum("otp_channel"), server_default="email", nullable=False),
        sa.Column("destination_masked", sa.String(length=80), nullable=False),
        sa.Column("otp_hmac", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "status", _enum("confirmation_request_status"), server_default="sent", nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('decision', 'second_approval')",
            name=op.f("ck_confirmation_requests_purpose_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_confirmation_requests_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"],
            ["hospitals.id"],
            name=op.f("fk_confirmation_requests_hospital_id_hospitals"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_staff_id"],
            ["hospital_staff.id"],
            name=op.f("fk_confirmation_requests_requested_by_staff_id_hospital_staff"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_confirmation_requests")),
    )
    op.create_index(
        op.f("ix_confirmation_requests_case_id"), "confirmation_requests", ["case_id"], unique=False
    )

    op.create_table(
        "consents",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("scope", _enum("consent_scope"), nullable=False),
        sa.Column("actor", _enum("consent_actor"), nullable=False),
        sa.Column("granted_by_id", sa.UUID(), nullable=False),
        sa.Column("wording_version", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hmac", sa.CHAR(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_consents_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"],
            ["users.id"],
            name=op.f("fk_consents_granted_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index(op.f("ix_consents_case_id"), "consents", ["case_id"], unique=False)
    op.create_index(
        "uq_consents_case_scope_active",
        "consents",
        ["case_id", "scope"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )

    op.create_table(
        "donations",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("paypal_order_id", sa.String(length=40), nullable=False),
        sa.Column("paypal_capture_id", sa.String(length=40), nullable=True),
        sa.Column("status", _enum("donation_status"), server_default="created", nullable=False),
        sa.Column("amount_usd_minor", sa.BigInteger(), nullable=False),
        sa.Column("paypal_fee_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("net_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("fx_rate_id", sa.UUID(), nullable=True),
        sa.Column("kes_equivalent_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("allocated_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("refunded_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("excess_choice", _enum("excess_choice"), server_default="refund", nullable=False),
        sa.Column("donor_display_name", sa.String(length=40), nullable=True),
        sa.Column("is_anonymous", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("donor_email_enc", sa.LargeBinary(), nullable=True),
        sa.Column("donor_email_hmac", sa.CHAR(length=64), nullable=True),
        sa.Column("receipt_token", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "allocated_usd_minor + refunded_usd_minor <= net_usd_minor",
            name=op.f("ck_donations_allocation_within_net"),
        ),
        sa.CheckConstraint(
            "allocated_usd_minor >= 0", name=op.f("ck_donations_allocated_non_negative")
        ),
        sa.CheckConstraint(
            "amount_usd_minor BETWEEN 100 AND 1000000", name=op.f("ck_donations_amount_range")
        ),
        sa.CheckConstraint("net_usd_minor >= 0", name=op.f("ck_donations_net_non_negative")),
        sa.CheckConstraint("paypal_fee_usd_minor >= 0", name=op.f("ck_donations_fee_non_negative")),
        sa.CheckConstraint(
            "refunded_usd_minor >= 0", name=op.f("ck_donations_refunded_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_donations_case_id_cases"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fx_rate_id"],
            ["fx_rates.id"],
            name=op.f("fk_donations_fx_rate_id_fx_rates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_donations")),
        sa.UniqueConstraint("paypal_capture_id", name=op.f("uq_donations_paypal_capture_id")),
        sa.UniqueConstraint("paypal_order_id", name=op.f("uq_donations_paypal_order_id")),
        sa.UniqueConstraint("receipt_token", name=op.f("uq_donations_receipt_token")),
    )
    op.create_index(op.f("ix_donations_case_id"), "donations", ["case_id"], unique=False)
    op.create_index("ix_donations_case_status", "donations", ["case_id", "status"], unique=False)
    op.create_index(
        op.f("ix_donations_donor_email_hmac"), "donations", ["donor_email_hmac"], unique=False
    )

    op.create_table(
        "ledger_entries",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=True),
        sa.Column("entry_type", _enum("ledger_entry_type"), nullable=False),
        sa.Column("account", _enum("ledger_account"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("ref_type", sa.String(length=40), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("memo", sa.String(length=200), nullable=True),
        sa.Column("prev_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("hash", sa.CHAR(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_minor <> 0", name=op.f("ck_ledger_entries_amount_non_zero")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_ledger_entries_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("hash", name=op.f("uq_ledger_entries_hash")),
        sa.UniqueConstraint("seq", name=op.f("uq_ledger_entries_seq")),
    )
    op.create_index(op.f("ix_ledger_entries_case_id"), "ledger_entries", ["case_id"], unique=False)
    op.create_index(
        "ix_ledger_entries_case_seq", "ledger_entries", ["case_id", "seq"], unique=False
    )
    op.create_index("uq_ledger_entries_prev_hash", "ledger_entries", ["prev_hash"], unique=True)

    # The ledger is append-only: any UPDATE or DELETE is rejected. TRUNCATE stays allowed
    # because the demo reset command relies on it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ledger_entries_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'ledger_entries is append-only';
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ledger_entries_append_only
          BEFORE UPDATE OR DELETE ON ledger_entries
          FOR EACH ROW EXECUTE FUNCTION ledger_entries_append_only()
        """
    )

    op.create_table(
        "review_items",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("source", _enum("review_source"), nullable=False),
        sa.Column("status", _enum("review_status"), server_default="open", nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("assigned_to_id", sa.UUID(), nullable=True),
        sa.Column("resolution", _enum("review_resolution"), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        sa.Column("resolved_by_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'resolved' OR (resolution IS NOT NULL AND resolved_by_id IS NOT NULL "
            "AND resolved_at IS NOT NULL "
            "AND length(btrim(coalesce(resolution_note, ''))) > 0)",
            name=op.f("ck_review_items_resolved_has_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_id"],
            ["users.id"],
            name=op.f("fk_review_items_assigned_to_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_review_items_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name=op.f("fk_review_items_resolved_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_items")),
    )
    op.create_index(op.f("ix_review_items_case_id"), "review_items", ["case_id"], unique=False)
    op.create_index(op.f("ix_review_items_status"), "review_items", ["status"], unique=False)

    op.create_table(
        "confirmations",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("hospital_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("confirmation_request_id", sa.UUID(), nullable=False),
        sa.Column("confirmed_by_staff_id", sa.UUID(), nullable=False),
        sa.Column("second_approver_staff_id", sa.UUID(), nullable=True),
        sa.Column("second_request_id", sa.UUID(), nullable=True),
        sa.Column("status", _enum("confirmation_status"), nullable=False),
        sa.Column("amount_kes_minor", sa.BigInteger(), nullable=True),
        sa.Column("invoice_number", sa.String(length=60), nullable=True),
        sa.Column("is_final_bill", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deposit_kes_minor", sa.BigInteger(), nullable=True),
        sa.Column("query_note", sa.String(length=500), nullable=True),
        sa.Column("paypal_invoice_id", sa.String(length=40), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'confirmed' OR finalized_at IS NOT NULL",
            name=op.f("ck_confirmations_confirmed_has_finalized_at"),
        ),
        sa.CheckConstraint(
            "status IN ('queried', 'rejected') OR amount_kes_minor IS NOT NULL",
            name=op.f("ck_confirmations_amount_required"),
        ),
        sa.CheckConstraint("amount_kes_minor > 0", name=op.f("ck_confirmations_amount_positive")),
        sa.CheckConstraint(
            "deposit_kes_minor >= 0", name=op.f("ck_confirmations_deposit_non_negative")
        ),
        sa.CheckConstraint(
            "second_approver_staff_id IS NULL OR second_approver_staff_id <> confirmed_by_staff_id",
            name=op.f("ck_confirmations_second_approver_differs"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_confirmations_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_request_id"],
            ["confirmation_requests.id"],
            name=op.f("fk_confirmations_confirmation_request_id_confirmation_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_staff_id"],
            ["hospital_staff.id"],
            name=op.f("fk_confirmations_confirmed_by_staff_id_hospital_staff"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["case_documents.id"],
            name=op.f("fk_confirmations_document_id_case_documents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"],
            ["hospitals.id"],
            name=op.f("fk_confirmations_hospital_id_hospitals"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["second_approver_staff_id"],
            ["hospital_staff.id"],
            name=op.f("fk_confirmations_second_approver_staff_id_hospital_staff"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["second_request_id"],
            ["confirmation_requests.id"],
            name=op.f("fk_confirmations_second_request_id_confirmation_requests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_confirmations")),
        sa.UniqueConstraint(
            "confirmation_request_id", name=op.f("uq_confirmations_confirmation_request_id")
        ),
        sa.UniqueConstraint("paypal_invoice_id", name=op.f("uq_confirmations_paypal_invoice_id")),
        sa.UniqueConstraint("second_request_id", name=op.f("uq_confirmations_second_request_id")),
    )
    op.create_index(op.f("ix_confirmations_case_id"), "confirmations", ["case_id"], unique=False)

    op.create_table(
        "refunds",
        sa.Column("donation_id", sa.UUID(), nullable=False),
        sa.Column("amount_usd_minor", sa.BigInteger(), nullable=False),
        sa.Column("reason", _enum("refund_reason"), nullable=False),
        sa.Column("status", _enum("refund_status"), server_default="pending", nullable=False),
        sa.Column("paypal_refund_id", sa.String(length=40), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("requested_by", _enum("actor_type"), nullable=False),
        sa.Column("failure_reason", sa.String(length=300), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_usd_minor > 0", name=op.f("ck_refunds_amount_positive")),
        sa.ForeignKeyConstraint(
            ["donation_id"],
            ["donations.id"],
            name=op.f("fk_refunds_donation_id_donations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_refunds_idempotency_key")),
        sa.UniqueConstraint("paypal_refund_id", name=op.f("uq_refunds_paypal_refund_id")),
    )
    op.create_index(op.f("ix_refunds_donation_id"), "refunds", ["donation_id"], unique=False)

    op.create_table(
        "triage_results",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column(
            "extracted", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "signals", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("risk_score", sa.SmallInteger(), nullable=False),
        sa.Column("risk_band", _enum("risk_band"), nullable=False),
        sa.Column("route", _enum("triage_route"), nullable=False),
        sa.Column(
            "reasons_en", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "reasons_sw", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("used_llm", sa.Boolean(), nullable=False),
        sa.Column(
            "model_versions",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "risk_score BETWEEN 0 AND 100", name=op.f("ck_triage_results_risk_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_triage_results_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["case_documents.id"],
            name=op.f("fk_triage_results_document_id_case_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triage_results")),
    )
    op.create_index(op.f("ix_triage_results_case_id"), "triage_results", ["case_id"], unique=False)
    op.create_index(
        op.f("ix_triage_results_document_id"), "triage_results", ["document_id"], unique=False
    )

    op.create_table(
        "settlements",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("confirmation_id", sa.UUID(), nullable=False),
        sa.Column("tranche_no", sa.SmallInteger(), nullable=False),
        sa.Column("adapter", _enum("settlement_method"), nullable=False),
        sa.Column("status", _enum("settlement_status"), server_default="pending", nullable=False),
        sa.Column("amount_usd_minor", sa.BigInteger(), nullable=False),
        sa.Column("amount_kes_minor", sa.BigInteger(), nullable=False),
        sa.Column("fx_rate_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("external_batch_id", sa.String(length=40), nullable=True),
        sa.Column("external_item_id", sa.String(length=40), nullable=True),
        sa.Column("failure_reason", sa.String(length=300), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_kes_minor > 0", name=op.f("ck_settlements_amount_kes_positive")),
        sa.CheckConstraint("amount_usd_minor > 0", name=op.f("ck_settlements_amount_usd_positive")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_settlements_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_id"],
            ["confirmations.id"],
            name=op.f("fk_settlements_confirmation_id_confirmations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fx_rate_id"],
            ["fx_rates.id"],
            name=op.f("fk_settlements_fx_rate_id_fx_rates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlements")),
        sa.UniqueConstraint("case_id", "tranche_no", name="uq_settlements_case_id_tranche_no"),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_settlements_idempotency_key")),
    )
    op.create_index(op.f("ix_settlements_case_id"), "settlements", ["case_id"], unique=False)

    op.create_table(
        "settlement_allocations",
        sa.Column("settlement_id", sa.UUID(), nullable=False),
        sa.Column("donation_id", sa.UUID(), nullable=False),
        sa.Column("amount_usd_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_usd_minor > 0", name=op.f("ck_settlement_allocations_amount_positive")
        ),
        sa.ForeignKeyConstraint(
            ["donation_id"],
            ["donations.id"],
            name=op.f("fk_settlement_allocations_donation_id_donations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.id"],
            name=op.f("fk_settlement_allocations_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "settlement_id", "donation_id", name=op.f("pk_settlement_allocations")
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_cases_photo_document_id_case_documents"), "cases", type_="foreignkey"
    )
    op.drop_table("settlement_allocations")
    op.drop_index(op.f("ix_settlements_case_id"), table_name="settlements")

    op.drop_table("settlements")
    op.drop_index(op.f("ix_triage_results_document_id"), table_name="triage_results")
    op.drop_index(op.f("ix_triage_results_case_id"), table_name="triage_results")

    op.drop_table("triage_results")
    op.drop_index(op.f("ix_refunds_donation_id"), table_name="refunds")

    op.drop_table("refunds")
    op.drop_index(op.f("ix_confirmations_case_id"), table_name="confirmations")

    op.drop_table("confirmations")
    op.drop_index(op.f("ix_review_items_status"), table_name="review_items")
    op.drop_index(op.f("ix_review_items_case_id"), table_name="review_items")

    op.drop_table("review_items")
    op.drop_index("uq_ledger_entries_prev_hash", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_case_seq", table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_case_id"), table_name="ledger_entries")

    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_donations_donor_email_hmac"), table_name="donations")
    op.drop_index("ix_donations_case_status", table_name="donations")
    op.drop_index(op.f("ix_donations_case_id"), table_name="donations")

    op.drop_table("donations")
    op.drop_index(
        "uq_consents_case_scope_active",
        table_name="consents",
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )
    op.drop_index(op.f("ix_consents_case_id"), table_name="consents")

    op.drop_table("consents")
    op.drop_index(op.f("ix_confirmation_requests_case_id"), table_name="confirmation_requests")

    op.drop_table("confirmation_requests")
    op.drop_index(op.f("ix_case_documents_sha256"), table_name="case_documents")
    op.drop_index(op.f("ix_case_documents_phash"), table_name="case_documents")
    op.drop_index(op.f("ix_case_documents_fingerprint"), table_name="case_documents")
    op.drop_index(op.f("ix_case_documents_case_id"), table_name="case_documents")

    op.drop_table("case_documents")
    op.drop_index(op.f("ix_appeal_checks_input_hmac"), table_name="appeal_checks")

    op.drop_table("appeal_checks")
    op.drop_index(op.f("ix_agent_actions_case_id"), table_name="agent_actions")

    op.drop_table("agent_actions")
    op.drop_index(op.f("ix_hospital_staff_hospital_id"), table_name="hospital_staff")

    op.drop_table("hospital_staff")
    op.drop_index(op.f("ix_cases_status"), table_name="cases")
    op.drop_index("ix_cases_hospital_status", table_name="cases")
    op.drop_index(op.f("ix_cases_hospital_id"), table_name="cases")
    op.drop_index(op.f("ix_cases_created_by_id"), table_name="cases")
    op.drop_index(
        "ix_cases_active_patient_ref",
        table_name="cases",
        postgresql_where=sa.text("status NOT IN ('closed','cancelled')"),
    )

    op.drop_table("cases")
    op.drop_table("hospitals")
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")

    op.drop_table("audit_log")
    op.drop_index(op.f("ix_webhook_events_resource_id"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_event_type"), table_name="webhook_events")

    op.drop_table("webhook_events")
    op.drop_index(op.f("ix_users_role"), table_name="users")

    op.drop_table("users")
    op.drop_index(op.f("ix_login_codes_email"), table_name="login_codes")

    op.drop_table("login_codes")
    op.drop_index("ix_fx_rates_pair_fetched_at", table_name="fx_rates")

    op.drop_table("fx_rates")
    op.drop_index(
        op.f("ix_facility_register_entries_normalized_name"), table_name="facility_register_entries"
    )

    op.drop_table("facility_register_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_append_only()")
    _drop_enums()
    op.execute("DROP EXTENSION IF EXISTS citext")
