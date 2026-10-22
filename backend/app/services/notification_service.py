"""Emails to the people on a case. They carry no diagnosis and no document content."""

import logging
from typing import Any

from flask import current_app

from app.models.case import Case
from app.models.enums import CloseReason
from app.services import mail_service

logger = logging.getLogger(__name__)


def notify_family(case: Case, template: str, **context: Any) -> bool:
    """Email the case owner in their language. Returns False if the mail server refused it.

    A failed email is logged and never undoes the action that caused it.
    """
    owner = case.created_by
    try:
        mail_service.send(
            template,
            owner.email,
            language=owner.preferred_language,
            alias=case.alias,
            **context,
        )
    except mail_service.MailError as exc:
        logger.error("family email failed", extra={"template": template, "reason": str(exc)})
        return False
    return True


def notify_closed(case: Case) -> bool:
    """Tell the family their case is closed because the hospital's bill is paid in full."""
    return notify_family(
        case,
        "case_closed",
        url=f"{current_app.config['APP_BASE_URL'].rstrip('/')}/c/{case.public_slug}",
        lower_bill=case.close_reason == CloseReason.FINAL_BILL_LOWER,
    )
