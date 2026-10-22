"""confirmations and settlements: track the PayPal invoice and its payments

A hospital that has PayPal credentials gets an invoice for each confirmed bill, and each tranche
that reaches it is recorded as a payment on that invoice. The confirmation keeps the invoice's
status and the USD amount it was issued for, and the settlement keeps the ID of the payment
recorded for it, so the same tranche is never recorded twice.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22

"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("confirmations", sa.Column("invoice_status", sa.String(length=24)))
    op.add_column("confirmations", sa.Column("invoice_usd_minor", sa.BigInteger()))
    op.add_column("settlements", sa.Column("invoice_payment_id", sa.String(length=40)))


def downgrade() -> None:
    op.drop_column("settlements", "invoice_payment_id")
    op.drop_column("confirmations", "invoice_usd_minor")
    op.drop_column("confirmations", "invoice_status")
