import pytest

from app.security.masking import mask_email


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("billing@kilele-hospital.test", "bil***@kilele-hospital.test"),
        ("ab@x.test", "a***@x.test"),
        ("abc@x.test", "ab***@x.test"),
        ("a@x.test", "a***@x.test"),
    ],
)
def test_mask_email_hides_most_of_the_local_part(email: str, masked: str) -> None:
    assert mask_email(email) == masked


def test_a_short_address_is_never_shown_whole() -> None:
    assert "abc" not in mask_email("abc@x.test")
