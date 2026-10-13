import pytest

from placex.ai.stub import KeywordBackend


@pytest.fixture
def backend():
    return KeywordBackend()


def test_extracts_skill_case_insensitively(backend):
    catalog = [{"id": 1, "name": "Python", "aliases": []}]

    result = backend.extract_skills("Experienced in PYTHON development", catalog)

    assert len(result) == 1
    assert result[0][0] == 1


def test_does_not_match_skill_inside_larger_word(backend):
    catalog = [{"id": 1, "name": "SQL", "aliases": []}]

    result = backend.extract_skills("I enjoy NoSQL databases", catalog)

    assert result == []


def test_matches_skill_alias(backend):
    catalog = [
        {"id": 1, "name": "PostgreSQL", "aliases": ["Postgres"]}
    ]

    result = backend.extract_skills("Built a Postgres database", catalog)

    assert len(result) == 1
    assert result[0][0] == 1


def test_single_letter_language_name_is_case_sensitive(backend):
    catalog = [{"id": 1, "name": "C", "aliases": []}]

    assert backend.extract_skills("I use C for systems programming", catalog)
    assert backend.extract_skills("I like cooking", catalog) == []


def test_repeated_skill_mentions_do_not_exceed_full_confidence(backend):
    catalog = [{"id": 1, "name": "Python", "aliases": []}]

    result = backend.extract_skills(
        "Python Python Python Python Python", catalog
    )

    assert result == [(1, 1.0)]
