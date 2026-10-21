"""Medical-term matching used by the story check (and, later, by redaction)."""

import pytest

from app import lexicon


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("She was diagnosed with cancer last year.", ["cancer"]),
        ("Please pray for his MALARIA", ["malaria"]),
        ("Operation after the appendicitis", ["appendicitis"]),
        ("breast cancer and chemotherapy", ["breast cancer", "chemotherapy"]),
        ("Breast   Cancer", ["breast cancer"]),
        ("Our family needs help with the bill", []),
        ("", []),
    ],
)
def test_terms_are_found_whole_and_case_insensitively(app, text: str, expected: list[str]) -> None:
    with app.app_context():
        assert lexicon.find_medical_terms(text) == expected


def test_the_longest_phrase_wins_over_its_parts(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        found = lexicon.find_medical_terms("She has breast cancer")
    assert found == ["breast cancer"]


def test_a_term_inside_a_longer_word_is_not_a_match(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert lexicon.find_medical_terms("A scancer? Cancerville is a town.") == []


def test_each_term_is_reported_once_in_order_of_appearance(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert lexicon.find_medical_terms("malaria, then cancer, then malaria again") == [
            "malaria",
            "cancer",
        ]


def test_accents_and_compatibility_forms_do_not_hide_a_term(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert lexicon.find_medical_terms("ｃａｎｃｅｒ") == ["cancer"]
        assert lexicon.find_medical_terms("cáncer".replace("á", "a")) == ["cancer"]


def test_swahili_terms_are_in_the_list(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        terms = lexicon.read_terms(lexicon.DEFAULT_LEXICON_DIR / "medical_terms.txt")
        assert "malaria" in terms
        assert any(term in terms for term in ("kisukari", "saratani", "homa ya matumbo"))


def test_comments_and_blank_lines_are_skipped(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "terms.txt"
    path.write_text("# a comment\n\nCancer\n  # indented comment\nMalaria  \n", encoding="utf-8")
    assert lexicon.read_terms(path) == ("cancer", "malaria")


def test_the_directory_can_be_configured(app, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "medical_terms.txt").write_text("zorbitis\n", encoding="utf-8")
    monkeypatch.setitem(app.config, "LEXICON_DIR", str(tmp_path))
    with app.app_context():
        assert lexicon.find_medical_terms("he has zorbitis and cancer") == ["zorbitis"]
