"""Templated email through SMTP (Mailpit locally).

Templates live in ``app/templates/email/<name>.<lang>.txt`` and ``.html``. No email carries a
diagnosis or document content. In tests ``MAIL_BACKEND`` is ``memory`` and messages collect in
``outbox`` instead of being sent.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass, replace
from email.message import EmailMessage
from typing import Any

from flask import current_app, render_template

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "sw")
DEFAULT_LANGUAGE = "en"
SMTP_TIMEOUT_SECONDS = 10

# Swahili wording is unreviewed; a native speaker checks it in P4-F10.
SUBJECTS: dict[str, dict[str, str]] = {
    "login_code": {
        "en": "Your TibaDirect sign-in code",
        "sw": "Msimbo wako wa kuingia TibaDirect",
    },
}


class MailError(Exception):
    """The message could not be handed to the mail server."""


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    template: str
    language: str


# Messages "sent" by the memory backend. Tests read and clear this list.
outbox: list[OutgoingEmail] = []


def _language(requested: str | None) -> str:
    return requested if requested in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def render(template: str, *, language: str, **context: Any) -> OutgoingEmail:
    """Render both parts of a template. The recipient is filled in by ``send``."""
    language = _language(language)
    return OutgoingEmail(
        to="",
        subject=SUBJECTS[template][language],
        text=render_template(f"email/{template}.{language}.txt", **context),
        html=render_template(f"email/{template}.{language}.html", **context),
        template=template,
        language=language,
    )


def send(template: str, to: str, *, language: str = DEFAULT_LANGUAGE, **context: Any) -> None:
    """Render ``template`` and send it to ``to``. Raises ``MailError`` if the server refuses."""
    rendered = render(template, language=language, **context)
    message = replace(rendered, to=to)
    backend = current_app.config.get("MAIL_BACKEND", "smtp")
    if backend == "memory":
        outbox.append(message)
    else:
        _send_smtp(message)
    logger.info("email sent", extra={"template": template, "language": message.language})


def _send_smtp(message: OutgoingEmail) -> None:
    config = current_app.config
    email = EmailMessage()
    email["From"] = config["MAIL_FROM"]
    email["To"] = message.to
    email["Subject"] = message.subject
    email.set_content(message.text)
    email.add_alternative(message.html, subtype="html")
    try:
        with smtplib.SMTP(
            config["SMTP_HOST"], config["SMTP_PORT"], timeout=SMTP_TIMEOUT_SECONDS
        ) as smtp:
            if config["SMTP_USERNAME"]:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
            smtp.send_message(email)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(type(exc).__name__) from exc
