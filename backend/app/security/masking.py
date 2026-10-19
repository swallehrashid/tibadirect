"""Masking for values shown back to users."""


def mask_email(email: str) -> str:
    """``billing@kilele-hospital.test`` becomes ``bil***@kilele-hospital.test``.

    At most half of a short local part is shown, so a very short address is never revealed whole.
    """
    local, _, domain = email.partition("@")
    visible = local[: min(3, (len(local) + 1) // 2)]
    return f"{visible}***@{domain}"
