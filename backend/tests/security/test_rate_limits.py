"""Anything a stranger can call over and over, or use to guess a secret, is rate limited.

Each case hammers one route from one address until it is told to slow down. The bound is the
documented limit with some room, so a deliberate change of limit shows up here.
"""

import pytest

from tests.browser import Browser

# (method, path, body, the most requests allowed before the first 429)
STRANGER_ROUTES = [
    ("post", "/api/auth/request-code", {"email": "someone@example.test"}, 5),
    ("post", "/api/auth/verify", {"email": "someone@example.test", "code": "000000"}, 10),
    ("post", "/api/donations/orders", {"case_slug": "nosuchcase1", "amount_usd": "10"}, 20),
    ("post", "/api/donations/orders/ORDER-1/capture", None, 40),
    ("get", "/api/donations/receipts/not-a-real-receipt-token", None, 60),
    ("get", "/api/public/cases/nosuchcase1", None, 120),
    ("get", "/api/public/cases/nosuchcase1/ledger", None, 120),
    ("get", "/api/public/cases/nosuchcase1/ledger/verify", None, 20),
    ("get", "/api/public/cases", None, 120),
    ("post", "/api/webhooks/paypal", {"id": "WH-1"}, 600),
    (
        "post",
        "/api/public/checker/appeal",
        {"text": "Please send money to 0700 000 000 urgently"},
        20,
    ),
]


@pytest.mark.parametrize(("method", "path", "body", "allowed"), STRANGER_ROUTES)
def test_a_stranger_cannot_hammer_a_route(
    browser: Browser, method: str, path: str, body: dict | None, allowed: int
) -> None:
    statuses = []
    for _ in range(allowed + 5):
        call = getattr(browser, method)
        response = call(path, body) if method == "post" else call(path)
        statuses.append(response.status_code)
        if response.status_code == 429:
            break
    assert statuses[-1] == 429, (path, statuses[-3:])
    assert len(statuses) - 1 <= allowed
    assert response.get_json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in response.headers


def test_one_address_slowed_down_does_not_slow_down_another(browser: Browser) -> None:
    for _ in range(6):
        browser.post("/api/auth/request-code", {"email": "a@example.test"})
    assert browser.post("/api/auth/request-code", {"email": "a@example.test"}).status_code == 429
    elsewhere = Browser(browser.client, remote_addr="203.0.113.77")
    elsewhere.token = browser.token
    assert elsewhere.post("/api/auth/request-code", {"email": "b@example.test"}).status_code != 429


def test_wrong_codes_for_one_email_lock_it_even_from_new_addresses(browser: Browser) -> None:
    """Guessing a six-digit code is limited per address of the account, not only per caller."""
    statuses = []
    for number in range(14):
        caller = Browser(browser.client, remote_addr=f"203.0.113.{number + 1}")
        caller.token = browser.token
        statuses.append(
            caller.post(
                "/api/auth/verify", {"email": "target@example.test", "code": "123456"}
            ).status_code
        )
    assert 429 in statuses
    assert statuses.index(429) <= 10
