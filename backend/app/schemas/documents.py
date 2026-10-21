"""Schemas for bill uploads and triage."""

import marshmallow as ma
from flask_smorest.fields import Upload

from app.models.enums import DocumentKind
from app.services import document_service


class UploadFileSchema(ma.Schema):
    file = Upload(required=True, metadata={"description": "A PDF, PNG or JPEG of at most 10 MB"})


class UploadFormSchema(ma.Schema):
    kind = ma.fields.Enum(DocumentKind, by_value=True, required=True)


class DocumentSchema(ma.Schema):
    """Metadata only: never the file name, the extracted text or a storage key."""

    id = ma.fields.UUID()
    kind = ma.fields.Function(lambda document: document.kind.value)
    status = ma.fields.Function(lambda document: document.status.value)
    mime_type = ma.fields.String()
    size_bytes = ma.fields.Integer()
    page_count = ma.fields.Integer(allow_none=True)
    created_at = ma.fields.DateTime()
    raw_available = ma.fields.Function(document_service.raw_available)
    preview_url = ma.fields.Function(
        document_service.preview_url,
        metadata={"description": "A five-minute link to your own upload while the original exists"},
    )


class DocumentListSchema(ma.Schema):
    items = ma.fields.List(ma.fields.Nested(DocumentSchema))


class TriageStatusSchema(ma.Schema):
    ready = ma.fields.Boolean(metadata={"description": "False until the automatic checks have run"})
    risk_band = ma.fields.String(allow_none=True)
    route = ma.fields.String(allow_none=True)
    reasons_en = ma.fields.List(ma.fields.String())
    reasons_sw = ma.fields.List(ma.fields.String())
    checked_at = ma.fields.DateTime(allow_none=True)
