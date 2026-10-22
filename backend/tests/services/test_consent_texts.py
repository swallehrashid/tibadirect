"""The wording families agree to: complete, versioned and shaped as the service expects."""

import pytest

from app.models.enums import ConsentScope
from app.services import consent_service
from app.services.consent_service import ConsentTextMissing, load_texts


def test_every_scope_has_wording_in_both_languages() -> None:
    texts = load_texts()
    assert set(texts) == {(scope, lang) for scope in ConsentScope for lang in ("en", "sw")}


@pytest.mark.parametrize("language", ["en", "sw"])
def test_each_text_has_a_version_a_title_and_a_body(language: str) -> None:
    for text in consent_service.current_texts(language):
        assert text.version == "v1"
        assert text.title
        assert not text.title.startswith("#")
        assert len(text.body.split()) > 30
        assert text.language == language


def test_current_texts_follow_the_scope_order() -> None:
    assert [t.scope for t in consent_service.current_texts("en")] == list(ConsentScope)


def test_the_english_wording_makes_the_promises_the_product_keeps() -> None:
    body = {t.scope: t.body.lower() for t in consent_service.current_texts("en")}
    assert "withdraw" in body[ConsentScope.HOSPITAL_VERIFICATION]
    assert "cancelled" in body[ConsentScope.HOSPITAL_VERIFICATION]
    assert "diagnosis" in body[ConsentScope.PUBLIC_PAGE]
    assert "hidden" in body[ConsentScope.PUBLIC_PAGE]
    assert "outside kenya" in body[ConsentScope.AI_PROCESSING_ABROAD]
    assert "removed" in body[ConsentScope.AI_PROCESSING_ABROAD]
    assert "alias" in body[ConsentScope.RECEIPT_SHARING]


def test_wording_that_mentions_a_diagnosis_only_says_it_is_not_kept_or_shown() -> None:
    for language_texts in (consent_service.current_texts("en"),):
        for text in language_texts:
            if "diagnosis" in text.body.lower():
                assert "does not collect" in text.body or "never shows" in text.body


def test_the_wording_is_marked_as_not_yet_reviewed() -> None:
    assert not any(text.reviewed for text in load_texts().values())


def test_a_wording_file_without_front_matter_is_a_packaging_error(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "public_page.en.md"
    bad.write_text("# Title\n\nbody", encoding="utf-8")
    with pytest.raises(ConsentTextMissing, match="front matter"):
        consent_service._parse(bad, ConsentScope.PUBLIC_PAGE, "en")


def test_a_wording_file_without_a_version_or_title_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    no_version = tmp_path / "a.md"
    no_version.write_text("---\nreviewed: false\n---\n# Title\n\nbody", encoding="utf-8")
    no_title = tmp_path / "b.md"
    no_title.write_text("---\nversion: v1\n---\njust text\n", encoding="utf-8")
    for path in (no_version, no_title):
        with pytest.raises(ConsentTextMissing):
            consent_service._parse(path, ConsentScope.PUBLIC_PAGE, "en")


def test_a_missing_language_is_reported(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(consent_service, "TEXTS_DIR", tmp_path)
    load_texts.cache_clear()
    try:
        with pytest.raises(ConsentTextMissing, match="No en wording"):
            load_texts()
    finally:
        load_texts.cache_clear()
