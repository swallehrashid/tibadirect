"""The appeal checker: paste a forwarded message (or a screenshot of it) and get a risk score."""

from flask import current_app, request
from flask.views import MethodView

from app.ai import appeals, llm, redact
from app.api.base import TibaBlueprint
from app.errors import NotFound, ValidationFailed
from app.extensions import db, limiter
from app.models.enums import AppealInputKind
from app.schemas.checker import AppealResultSchema, AppealTextSchema
from app.security import uploads

blp = TibaBlueprint(
    "checker", __name__, url_prefix="/api/public/checker", description="Check a forwarded appeal"
)

CHECK_LIMIT = "20 per hour"
MIN_TEXT = 20
IMAGE_TYPES = ("image/png", "image/jpeg")


def _read_input() -> tuple[str, AppealInputKind]:
    """The message text, from JSON ``{"text": ...}`` or a multipart ``image`` (read with OCR)."""
    upload = request.files.get("image")
    if upload is not None:
        data = upload.read()
        checked = uploads.validate_upload(
            data, filename=upload.filename, declared_type=upload.mimetype
        )
        if checked.mime_type not in IMAGE_TYPES:
            raise ValidationFailed(
                "Upload a screenshot as a PNG or JPEG.",
                code="image_required",
                details={"json": {"image": ["Upload a PNG or JPEG."]}},
            )
        message = appeals.read_image(data, checked.mime_type)
        if len(message.strip()) < MIN_TEXT:
            raise ValidationFailed(
                "We could not read enough text in that picture. Paste the message instead.",
                code="image_unreadable",
                details={"json": {"image": ["Not enough readable text."]}},
            )
        return message, AppealInputKind.IMAGE
    payload = request.get_json(silent=True)
    errors = AppealTextSchema().validate(payload if isinstance(payload, dict) else {})
    if errors:
        raise ValidationFailed(details={"json": errors}, code="validation_error")
    return payload["text"], AppealInputKind.TEXT


@blp.route("/appeal")
class CheckAppeal(MethodView):
    decorators = [limiter.limit(CHECK_LIMIT, scope="appeal_check")]

    @blp.doc(
        requestBody={
            "required": True,
            "content": {
                "application/json": {"schema": AppealTextSchema},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"image": {"type": "string", "format": "binary"}},
                    }
                },
            },
        },
        security=[],
    )
    @blp.response(200, AppealResultSchema)
    @blp.alt_response(404, description="The checker is switched off")
    @blp.alt_response(422, description="No readable message")
    @blp.alt_response(429, description="More than 20 checks an hour from this address")
    def post(self) -> dict:
        """Score a forwarded appeal. What you send is hashed and never stored."""
        config = current_app.config
        if not config["APPEAL_CHECKER_ENABLED"]:
            raise NotFound("The appeal checker is not available.", code="feature_disabled")
        message, kind = _read_input()
        verdict, row = appeals.check(db.session, value=message, kind=kind)

        explanation = None
        if config["APPEAL_CHECKER_USE_LLM"]:
            # A third party's text, so it goes to the model only when this is switched on, and
            # only after the personal details in it are taken out.
            explanation = llm.explain_appeal(redact.redact(message).text, verdict.reasons_en)
            row.used_llm = explanation is not None
        db.session.commit()

        case = verdict.matched_case
        return {
            "score": verdict.score,
            "band": verdict.band.value,
            "flagged": verdict.flagged,
            "reasons_en": verdict.reasons_en,
            "reasons_sw": verdict.reasons_sw,
            "matched_case": (
                {
                    "slug": case.public_slug,
                    "alias": case.alias,
                    "hospital_name": case.hospital.display_name,
                    "url": appeals.app_url(case),
                    "note": "This appeal matches a hospital-confirmed TibaDirect case.",
                }
                if case
                else None
            ),
            "safety_tip_en": appeals.SAFETY_TIP[0],
            "safety_tip_sw": appeals.SAFETY_TIP[1],
            "explanation": explanation,
            "used_llm": explanation is not None,
        }
