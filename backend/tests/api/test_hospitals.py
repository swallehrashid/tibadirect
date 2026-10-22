"""The hospital directory for the case wizard."""

from tests.factories import FacilityRegisterEntryFactory, HospitalFactory


def _names(response) -> list[str]:  # type: ignore[no-untyped-def]
    return [item["display_name"] for item in response.get_json()["items"]]


def test_the_list_is_public_sorted_and_shows_only_what_the_wizard_needs(client, db_session) -> None:  # type: ignore[no-untyped-def]
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Zeta Mission Hospital"))
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Alpha Mission Hospital"))
    response = client.get("/api/hospitals")
    assert response.status_code == 200
    body = response.get_json()
    names = _names(response)
    assert names == sorted(names)
    assert names.index("Alpha Mission Hospital") < names.index("Zeta Mission Hospital")
    assert set(body["items"][0]) == {"id", "display_name", "county"}
    assert {"page", "page_size", "total"} <= set(body)


def test_inactive_hospitals_are_not_listed(client, db_session) -> None:  # type: ignore[no-untyped-def]
    HospitalFactory(
        register_entry=FacilityRegisterEntryFactory(name="Closed Down Hospital"), is_active=False
    )
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Open Doors Hospital"))
    names = _names(client.get("/api/hospitals?query=hospital"))
    assert "Open Doors Hospital" in names
    assert "Closed Down Hospital" not in names


def test_the_query_matches_part_of_a_name_without_regard_to_case(client, db_session) -> None:  # type: ignore[no-untyped-def]
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Kilele Mission Hospital"))
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Ziwa Bluu General"))
    assert _names(client.get("/api/hospitals?query=kILele")) == ["Kilele Mission Hospital"]
    assert _names(client.get("/api/hospitals?query=%20%20bluu%20")) == ["Ziwa Bluu General"]


def test_the_query_also_matches_the_county(client, db_session) -> None:  # type: ignore[no-untyped-def]
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Lake View", county="Kisumu"))
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Hill Top", county="Nyeri"))
    assert _names(client.get("/api/hospitals?query=kisumu")) == ["Lake View"]


def test_wildcard_characters_in_the_query_are_just_characters(client, db_session) -> None:  # type: ignore[no-untyped-def]
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Plain Hospital"))
    HospitalFactory(register_entry=FacilityRegisterEntryFactory(name="Fifty% Care Clinic"))
    assert _names(client.get("/api/hospitals?query=%25")) == ["Fifty% Care Clinic"]
    assert _names(client.get("/api/hospitals?query=_")) == []
    assert _names(client.get("/api/hospitals?query=%5C")) == []


def test_an_unmatched_query_is_an_empty_page_not_an_error(client, db_session) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/hospitals?query=nothing-like-this").get_json()
    assert body["items"] == []
    assert body["total"] == 0


def test_an_overlong_query_is_a_validation_error(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/hospitals?query=" + "x" * 81).status_code == 422


def test_the_list_is_paginated(client, db_session) -> None:  # type: ignore[no-untyped-def]
    for index in range(5):
        HospitalFactory(register_entry=FacilityRegisterEntryFactory(name=f"Paged Hospital {index}"))
    first = client.get("/api/hospitals?query=paged&page_size=2").get_json()
    second = client.get("/api/hospitals?query=paged&page_size=2&page=2").get_json()
    assert (first["total"], len(first["items"]), len(second["items"])) == (5, 2, 2)
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


def test_the_register_email_is_never_exposed(client, db_session) -> None:  # type: ignore[no-untyped-def]
    HospitalFactory(
        register_entry=FacilityRegisterEntryFactory(
            name="Secretive Hospital", official_email="billing@secretive.test"
        )
    )
    assert "secretive.test" not in client.get("/api/hospitals?query=secretive").get_data(
        as_text=True
    )
