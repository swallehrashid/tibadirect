"""Shared setup for hospital confirmation tests."""

import re
from dataclasses import dataclass
from typing import Any

from app.models.enums import CaseStatus, ConsentScope, StaffRole
from app.security.masking import mask_email
from app.services import mail_service
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    ConsentFactory,
    FacilityRegisterEntryFactory,
    HospitalFactory,
    HospitalStaffFactory,
)

REGISTER_EMAIL = "billing@kilele-hospital.test"
CLERK_EMAIL = "clerk@kilele-hospital.test"
SUPERVISOR_EMAIL = "supervisor@kilele-hospital.test"


@dataclass
class Ward:
    """One hospital with a clerk, a supervisor and a case waiting for its decision."""

    hospital: Any
    clerk: Any
    supervisor: Any
    case: Any
    document: Any


def make_ward(
    *,
    public_consent: bool = True,
    threshold_kes: int | None = None,
    other: bool = False,
    **case_fields: Any,
) -> Ward:
    """A hospital, its clerk and supervisor, and a case waiting for the hospital's decision.

    ``other=True`` builds a second, unrelated hospital with generated addresses, because the
    first one uses fixed addresses that tests look for.
    """
    if other:
        entry = FacilityRegisterEntryFactory()
        clerk_user: dict[str, Any] = {"user__display_name": "Other Clerk"}
        supervisor_user: dict[str, Any] = {"user__display_name": "Other Supervisor"}
    else:
        entry = FacilityRegisterEntryFactory(
            name="Kilele Mission Hospital", official_email=REGISTER_EMAIL, county="Nairobi"
        )
        clerk_user = {"user__email": CLERK_EMAIL, "user__display_name": "Clerk One"}
        supervisor_user = {"user__email": SUPERVISOR_EMAIL, "user__display_name": "Supervisor Two"}
    extra = {} if threshold_kes is None else {"two_person_threshold_kes_minor": threshold_kes * 100}
    hospital = HospitalFactory(register_entry=entry, **extra)
    clerk = HospitalStaffFactory(
        hospital=hospital, staff_role=StaffRole.BILLING_CLERK, **clerk_user
    )
    supervisor = HospitalStaffFactory(
        hospital=hospital, staff_role=StaffRole.BILLING_SUPERVISOR, **supervisor_user
    )
    fields = {"status": CaseStatus.PENDING_CONFIRMATION, "alias": "Baby Zawadi", **case_fields}
    owner = {} if other else {"created_by__email": "mama@example.test"}
    case = CaseFactory(hospital=hospital, **owner, **fields)
    document = CaseDocumentFactory(case=case)
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    if public_consent:
        ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    return Ward(hospital, clerk, supervisor, case, document)


def last_code_sent_to(address: str) -> str:
    """The six-digit code in the newest email to ``address``."""
    messages = [m for m in mail_service.outbox if m.to == address]
    assert messages, f"no email was sent to {address}"
    match = re.search(r"is (\d{6})\.", messages[-1].text)
    assert match, messages[-1].text
    return match.group(1)


def masked(address: str) -> str:
    return mask_email(address)
