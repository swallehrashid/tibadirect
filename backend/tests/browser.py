"""A test client that behaves like the single-page app: fetch a CSRF token, send it, repeat."""

from typing import Any

from flask.testing import FlaskClient
from flask.wrappers import Response

from app.services import mail_service


class Browser:
    """A test client that behaves like the single-page app: fetch a token, send it, repeat."""

    def __init__(self, client: FlaskClient, remote_addr: str = "198.51.100.10") -> None:
        self.client = client
        self.remote_addr = remote_addr
        self.token: str | None = None

    def refresh_csrf(self) -> str:
        self.token = self.client.get("/api/auth/csrf").get_json()["csrf_token"]
        return self.token

    def get(self, path: str, **kwargs: Any) -> Response:
        kwargs.setdefault("environ_overrides", {"REMOTE_ADDR": self.remote_addr})
        return self.client.get(path, **kwargs)

    def _send(self, method: str, path: str, **kwargs: Any) -> Response:
        if self.token is None:
            self.refresh_csrf()
        headers = {"X-CSRFToken": self.token, **kwargs.pop("headers", {})}
        kwargs.setdefault("environ_overrides", {"REMOTE_ADDR": self.remote_addr})
        return getattr(self.client, method)(path, headers=headers, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs: Any) -> Response:
        return self._send("post", path, json=json, **kwargs)

    def patch(self, path: str, json: Any = None, **kwargs: Any) -> Response:
        return self._send("patch", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Response:
        return self._send("delete", path, **kwargs)

    def sign_in_by_email(self, email: str) -> Response:
        """The real flow: request a code, read it from the outbox, verify it."""
        mail_service.outbox.clear()
        self.post("/api/auth/request-code", {"email": email})
        code = next(m.text for m in mail_service.outbox if m.to == email.lower()).split("is ")[1][
            :6
        ]
        response = self.post("/api/auth/verify", {"email": email, "code": code})
        self.refresh_csrf()
        return response
