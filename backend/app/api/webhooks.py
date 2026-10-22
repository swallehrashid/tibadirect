"""The PayPal webhook receiver. PayPal calls it; there is no session and no CSRF token."""

import marshmallow as ma
from flask import request
from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import csrf, db, limiter
from app.payments import webhooks

blp = TibaBlueprint(
    "webhooks", __name__, url_prefix="/api/webhooks", description="Events sent by PayPal"
)
# PayPal proves who it is with a signature, not a cookie, so the CSRF check does not apply.
csrf.exempt(blp)

# PayPal sends from many addresses and bursts when it retries; this only stops floods.
WEBHOOK_LIMIT = "600 per minute"


class WebhookAckSchema(ma.Schema):
    status = ma.fields.String(
        metadata={"description": "received for a new event, duplicate for one seen before"}
    )


@blp.route("/paypal")
class PayPalWebhook(MethodView):
    decorators = [limiter.limit(WEBHOOK_LIMIT, scope="webhook")]

    @blp.doc(
        requestBody={
            "required": True,
            "description": "A PayPal webhook event, exactly as PayPal sent it",
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
        security=[],
    )
    @blp.response(200, WebhookAckSchema)
    @blp.alt_response(400, description="Not an event, or the signature did not verify")
    @blp.alt_response(502, description="PayPal could not be asked to verify; it will resend")
    def post(self) -> dict:
        """Verify the signature through PayPal, store the event once and process it in the
        background. Replaying an event does nothing. Always answers fast."""
        try:
            received = webhooks.receive(db.session, request.get_data(), request.headers)
        except webhooks.WebhookRejected:
            db.session.commit()  # a forged attempt is kept in the log
            raise
        db.session.commit()
        if received.duplicate:
            return {"status": "duplicate"}
        webhooks.enqueue_processing(received.event.id)
        return {"status": "received"}
