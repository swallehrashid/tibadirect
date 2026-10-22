"""donations: record money moved to the solidarity pool

A donor may choose that money the case does not need goes to the solidarity pool, and a refund
too small to send back goes there as well. The amount is recorded on the donation, next to what
was paid out and what was refunded, and the rule that those three never add up to more than the
net amount now includes it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22

"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "donations",
        sa.Column("pooled_usd_minor", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_donations_pooled_non_negative"), "donations", "pooled_usd_minor >= 0"
    )
    op.drop_constraint(op.f("ck_donations_allocation_within_net"), "donations", type_="check")
    op.create_check_constraint(
        op.f("ck_donations_allocation_within_net"),
        "donations",
        "allocated_usd_minor + refunded_usd_minor + pooled_usd_minor <= net_usd_minor",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_donations_allocation_within_net"), "donations", type_="check")
    op.create_check_constraint(
        op.f("ck_donations_allocation_within_net"),
        "donations",
        "allocated_usd_minor + refunded_usd_minor <= net_usd_minor",
    )
    op.drop_constraint(op.f("ck_donations_pooled_non_negative"), "donations", type_="check")
    op.drop_column("donations", "pooled_usd_minor")
