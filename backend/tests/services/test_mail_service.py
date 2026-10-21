"""Templated email: rendering in both languages and the SMTP path."""

import smtplib
from email import message_from_bytes
from email.message import EmailMessage
from typing import Any

import pytest
from flask import Flask

from app.services import mail_service


@pytest.fixture(autouse=True)
def _clean_outbox() -> None:
    mail_service.outbox.clear()


def test_memory_backend_collects_the_message(app: Flask) -> None:
    with app.app_context():
        mail_service.send("login_code", "a@example.test", code="123456", minutes=10)
    [message] = mail_service.outbox
    assert message.to == "a@example.test"
    assert message.subject == "Your TibaDirect sign-in code"
    assert "123456" in message.text
    assert "123456" in message.html
    assert "10 minutes" in message.text


def test_swahili_template_is_used_when_asked(app: Flask) -> None:
    with app.app_context():
        mail_service.send("login_code", "a@example.test", language="sw", code="654321", minutes=10)
    [message] = mail_service.outbox
    assert message.language == "sw"
    assert message.subject == "Msimbo wako wa kuingia TibaDirect"
    assert "654321" in message.text


@pytest.mark.parametrize("language", ["fr", "", None])
def test_unknown_language_falls_back_to_english(app: Flask, language: str | None) -> None:
    with app.app_context():
        mail_service.send("login_code", "a@example.test", language=language, code="1", minutes=10)  # type: ignore[arg-type]
    assert mail_service.outbox[0].language == "en"


def test_html_part_escapes_values(app: Flask) -> None:
    with app.app_context():
        mail_service.send("login_code", "a@example.test", code="<b>1</b>", minutes=10)
    assert "<b>1</b>" not in mail_service.outbox[0].html
    assert "&lt;b&gt;" in mail_service.outbox[0].html


def test_every_template_exists_in_both_languages(app: Flask) -> None:
    with app.app_context():
        for template in mail_service.SUBJECTS:
            for language in mail_service.SUPPORTED_LANGUAGES:
                rendered = mail_service.render(
                    template, language=language, code="123456", minutes=10
                )
                assert rendered.text.strip()
                assert rendered.html.strip()


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def starttls(self, context: Any) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)


@pytest.fixture
def smtp(app: Flask, monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "smtp")
    return FakeSMTP


def test_smtp_sends_a_multipart_message_without_auth_for_mailpit(
    app: Flask, smtp: type[FakeSMTP]
) -> None:
    with app.app_context():
        mail_service.send("login_code", "a@example.test", code="123456", minutes=10)
    [server] = smtp.instances
    assert (server.host, server.port) == (app.config["SMTP_HOST"], app.config["SMTP_PORT"])
    assert server.timeout == mail_service.SMTP_TIMEOUT_SECONDS
    assert server.logged_in is None
    assert not server.started_tls
    [message] = server.sent
    assert message["To"] == "a@example.test"
    assert message["From"] == app.config["MAIL_FROM"]
    parsed = message_from_bytes(message.as_bytes())
    assert parsed.is_multipart()
    assert {part.get_content_type() for part in parsed.walk()} >= {"text/plain", "text/html"}
    assert not mail_service.outbox


def test_smtp_with_credentials_uses_tls_and_logs_in(
    app: Flask, smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(app.config, "SMTP_USERNAME", "mailer")
    monkeypatch.setitem(app.config, "SMTP_PASSWORD", "pw")
    with app.app_context():
        mail_service.send("login_code", "a@example.test", code="123456", minutes=10)
    [server] = smtp.instances
    assert server.started_tls
    assert server.logged_in == ("mailer", "pw")


@pytest.mark.parametrize("error", [ConnectionRefusedError("down"), smtplib.SMTPException("no")])
def test_smtp_failure_becomes_a_mail_error_without_details(
    app: Flask, smtp: type[FakeSMTP], error: Exception
) -> None:
    def refuse(self: FakeSMTP, message: EmailMessage) -> None:
        raise error

    smtp.send_message = refuse  # type: ignore[method-assign]
    with app.app_context(), pytest.raises(mail_service.MailError) as raised:
        mail_service.send("login_code", "secret.person@example.test", code="1", minutes=10)
    assert "secret.person" not in str(raised.value)
