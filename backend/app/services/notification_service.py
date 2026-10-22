"""Emails to the people on a case. They carry no diagnosis and no document content."""

import logging
from typing import Any

from app.models.case import Case
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
