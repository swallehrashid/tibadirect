"""Blueprint base class for TibaDirect routes."""

from collections.abc import Callable
from typing import Any

from flask_smorest import Blueprint

from app.models.enums import UserRole
from app.schemas.errors import ErrorEnvelopeSchema
from app.security.permissions import login_required, roles_required


class TibaBlueprint(Blueprint):
    """A flask-smorest blueprint with one way to protect a route and document it.

    ``@blp.secured()`` needs a signed-in user; ``@blp.secured(UserRole.FAMILY)`` also needs that
    role. Put it above the other decorators so the check runs before the input is validated.
    """

    def secured(self, *roles: UserRole) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
            guarded = roles_required(*roles)(view) if roles else login_required(view)
            guarded = self.alt_response(
                401, schema=ErrorEnvelopeSchema, description="Not signed in"
            )(guarded)
            if roles:
                guarded = self.alt_response(
                    403, schema=ErrorEnvelopeSchema, description="Not allowed for this role"
                )(guarded)
            return self.doc(security=[{"sessionCookie": []}])(guarded)

        return decorator
