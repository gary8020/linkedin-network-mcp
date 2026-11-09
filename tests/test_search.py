from __future__ import annotations

from linkedin_mcp.server import LinkedInQueries, plain_text_to_fts


def snippets(hits) -> set[str]:
    return {hit.snippet for hit in hits}


def test_prefix_match_finds_word_variants(queries: LinkedInQueries) -> None:
    found = snippets(queries.search_messages("sponsor"))
    assert found == {
        "Interested in sponsoring the summit booth?",
        "Yes, sending the sponsorship deck now.",
    }


def test_prefix_match_is_case_insensitive_and_ands_terms(queries: LinkedInQueries) -> None:
    assert snippets(queries.search_messages("SPONSOR deck")) == {"Yes, sending the sponsorship deck now."}


def test_substring_fallback_when_fts_finds_nothing(queries: LinkedInQueries) -> None:
    assert snippets(queries.search_messages("ponsorsh")) == {"Yes, sending the sponsorship deck now."}


def test_no_results_still_returns_empty(queries: LinkedInQueries) -> None:
    assert queries.search_messages("zzzz-not-present") == []
    assert queries.search_messages("   ") == []


def test_like_wildcards_are_literal(queries: LinkedInQueries) -> None:
    assert queries.search_messages("%") == []
    assert queries.search_messages("_") == []


def test_japanese_substring_search(queries: LinkedInQueries) -> None:
    assert snippets(queries.search_messages("スポンサー")) == {"スポンサーシップの件でご相談です"}
    assert snippets(queries.search_messages("資料")) == {"ありがとうございます。資料を送ります。"}
    assert snippets(queries.search_messages("ご相談 スポンサー")) == {"スポンサーシップの件でご相談です"}


def test_japanese_search_respects_person_filter(queries: LinkedInQueries) -> None:
    alice = queries.find_person("Alice Agency", limit=1)[0]
    assert queries.search_messages("スポンサー", person_id=alice.id) == []


def test_search_date_filters(queries: LinkedInQueries) -> None:
    assert snippets(queries.search_messages("sponsor", since="2024-01-11")) == {
        "Yes, sending the sponsorship deck now."
    }
    assert snippets(queries.search_messages("sponsor", until="2024-01-10 23:59:59")) == {
        "Interested in sponsoring the summit booth?"
    }


def test_advanced_fts_passthrough(queries: LinkedInQueries) -> None:
    assert snippets(queries.search_messages("sourcing NOT trip", advanced_fts=True)) == {
        "Quick question about your sourcing service"
    }


def test_plain_text_to_fts_quotes_and_prefixes() -> None:
    assert plain_text_to_fts('say "hi" there') == '"say"* AND "hi"* AND "there"*'
