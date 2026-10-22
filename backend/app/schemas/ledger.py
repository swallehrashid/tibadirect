"""Schemas for the ledger views."""

import marshmallow as ma


class PublicLedgerRowSchema(ma.Schema):
    """One line of a case's public ledger. No memo, no full hash, no IDs."""

    created_at = ma.fields.DateTime()
    entry_type = ma.fields.String()
    account = ma.fields.String()
    amount_minor = ma.fields.Integer(
        metadata={"description": "Signed minor units: positive into the account, negative out"}
    )
    amount = ma.fields.String()
    currency = ma.fields.String()
    hash_short = ma.fields.String(metadata={"description": "First 12 characters of the entry hash"})
    reference = ma.fields.String(
        allow_none=True, metadata={"description": "PayPal or payout reference, shortened"}
    )
    donor_name = ma.fields.String(
        allow_none=True,
        metadata={"description": "Only on the donation line, and only if the donor gave a name"},
    )


class CaseVerificationSchema(ma.Schema):
    valid = ma.fields.Boolean(
        metadata={
            "description": "True only if the whole chain is intact and has this case's entries"
        }
    )
    entries_checked = ma.fields.Integer(metadata={"description": "This case's entries"})
    last_hash = ma.fields.String(
        metadata={"description": "Hash of this case's newest entry (zeros if it has none)"}
    )
    checked_at = ma.fields.DateTime()


class ChainVerificationSchema(ma.Schema):
    valid = ma.fields.Boolean()
    entries_checked = ma.fields.Integer()
    last_hash = ma.fields.String()
    first_bad_seq = ma.fields.Integer(allow_none=True)
    checked_at = ma.fields.DateTime()
