"""Consent wording and a family's consents on their own case."""

import uuid

from flask import request
from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.errors import NotFound
from app.extensions import db
from app.models.case import Consent
from app.models.enums import ConsentScope, UserRole
from app.schemas.consents import (
    ConsentListSchema,
    ConsentSchema,
    ConsentTextsQuerySchema,
    ConsentTextsSchema,
    GrantConsentSchema,
)
from app.security.permissions import current_account, get_owned_case_or_404
from app.services import consent_service

blp = TibaBlueprint("consents", __name__, url_prefix="/api", description="Consent")


def serialize_consent(consent: Consent) -> dict:
    return {
        "id": consent.id,
        "scope": consent.scope.value,
        "actor": consent.actor.value,
        "wording_version": consent.wording_version,
        "language": consent.language,
        "granted_at": consent.granted_at,
        "withdrawn_at": consent.withdrawn_at,
        "active": consent.withdrawn_at is None,
    }


@blp.route("/consents/texts")
class ConsentTexts(MethodView):
    @blp.arguments(ConsentTextsQuerySchema, location="query")
    @blp.response(200, ConsentTextsSchema)
    def get(self, args: dict) -> dict:
        """The current wording and version of every consent, in English or Swahili."""
        language = args["lang"]
        items = [
            {
                "scope": text.scope.value,
                "language": text.language,
                "version": text.version,
                "title": text.title,
                "body": text.body,
            }
            for text in consent_service.current_texts(language)
        ]
        return {"language": language, "items": items}


@blp.route("/cases/<uuid:case_id>/consents")
class CaseConsents(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(200, ConsentListSchema)
    def get(self, case_id: uuid.UUID) -> dict:
        """Active and withdrawn consents on this case."""
        case = get_owned_case_or_404(case_id, current_account())
        return {"items": [serialize_consent(c) for c in consent_service.list_for_case(case)]}

    @blp.secured(UserRole.FAMILY)
    @blp.arguments(GrantConsentSchema)
    @blp.response(201, ConsentSchema)
    @blp.alt_response(
        409, description="Outdated wording, already given, guardian needed or case closed"
    )
    def post(self, payload: dict, case_id: uuid.UUID) -> dict:
        """Give consent to the current wording of one scope."""
        user = current_account()
        case = get_owned_case_or_404(case_id, user)
        consent = consent_service.grant(db.session, case, user, ip=request.remote_addr, **payload)
        db.session.commit()
        return serialize_consent(consent)


@blp.route("/cases/<uuid:case_id>/consents/<string:scope>")
class CaseConsent(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(200, ConsentSchema)
    @blp.alt_response(404, description="No active consent for that scope")
    def delete(self, case_id: uuid.UUID, scope: str) -> dict:
        """Withdraw a consent. Withdrawing the public page hides it at once; withdrawing hospital
        verification before the hospital confirms cancels the case."""
        user = current_account()
        case = get_owned_case_or_404(case_id, user)
        try:
            parsed = ConsentScope(scope)
        except ValueError:
            raise NotFound(
                "There is no consent with that name.", code="consent_not_found"
            ) from None
        consent = consent_service.withdraw(db.session, case, user, parsed)
        db.session.commit()
        return serialize_consent(consent)
