from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.config import (
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_TOML,
    DEFAULT_WARM_LIST_KEYWORDS,
    load_config,
    write_default_config,
)
from linkedin_mcp.server import LinkedInQueries, keyword_pattern


def ranked_names(leads) -> list[str]:
    return [lead.person.full_name for lead in leads]


def test_warm_list_ranks_all_connections(queries: LinkedInQueries) -> None:
    leads = queries.warm_list(limit=100)
    assert len(leads) == 8
    assert [lead.rank for lead in leads] == list(range(1, 9))
    assert "Never Messaged" in ranked_names(leads)
    assert "Dana Dentist" in ranked_names(leads)
    scores = [lead.score for lead in leads]
    assert scores == sorted(scores, reverse=True)


def test_warm_list_default_keyword_ordering(queries: LinkedInQueries) -> None:
    leads = {lead.person.full_name: lead for lead in queries.warm_list(limit=100)}
    assert ranked_names(queries.warm_list(limit=3)) == ["Alice Agency", "Fiona Shopify", "Bob Saas"]
    assert leads["Alice Agency"].matched_keywords == ["agency", "amazon", "founder"]
    assert leads["Bob Saas"].matched_keywords == ["saas", "partnership", "head of"]
    assert leads["Fiona Shopify"].matched_keywords == ["shopify", "ecommerce"]
    assert leads["Evan Logistics"].matched_keywords == ["3pl"]
    assert leads["Hiro Sato"].matched_keywords == ["ceo"]
    assert leads["Dana Dentist"].matched_keywords == []
    assert leads["Never Messaged"].score > leads["Dana Dentist"].score


def test_warm_list_exposes_explainable_components(queries: LinkedInQueries) -> None:
    alice = queries.warm_list(limit=1)[0]
    assert set(alice.components) == {"keyword", "dm_history", "endorsements", "connected_recency"}
    assert alice.components["keyword"] == 1.0
    assert alice.messages_sent == 1 and alice.messages_received == 1
    assert alice.endorsements == 3
    assert alice.connected_on == "2023-01-01"
    assert alice.last_inbound_snippet == "Interested in sponsoring the summit booth?"


def test_warm_list_require_keyword_and_min_score(queries: LinkedInQueries) -> None:
    names = ranked_names(queries.warm_list(require_keyword=True, limit=100))
    assert "Dana Dentist" not in names
    assert "太郎 山田" not in names
    assert len(names) == 6
    assert all(lead.score >= 0.5 for lead in queries.warm_list(min_score=0.5, limit=100))


def test_warm_list_keyword_override_per_call(queries: LinkedInQueries) -> None:
    leads = queries.warm_list(keywords=["dentist"], require_keyword=True)
    assert ranked_names(leads) == ["Dana Dentist"]


def test_japanese_keyword_matches_substring(queries: LinkedInQueries) -> None:
    leads = queries.warm_list(keywords=["代表"], require_keyword=True)
    assert ranked_names(leads) == ["太郎 山田"]


def test_config_file_changes_keywords_and_weights(fixture_db: Path, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[warm_list]
keywords = ["dentist"]
[warm_list.weights]
keyword = 1.0
dm_history = 0
endorsements = 0
connected_recency = 0
""",
        encoding="utf-8",
    )
    leads = LinkedInQueries(fixture_db, config_path=config).warm_list(limit=100)
    assert leads[0].person.full_name == "Dana Dentist"
    assert leads[0].score == pytest.approx(0.5)
    assert all(lead.score == 0 for lead in leads[1:])


def test_config_env_var_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "env.toml"
    config.write_text('[warm_list]\nkeywords = ["x"]\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config))
    assert load_config().warm_list.keywords == ("x",)


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nope.toml")
    assert config.source is None
    assert config.warm_list.keywords == DEFAULT_WARM_LIST_KEYWORDS


@pytest.mark.parametrize(
    "body, message",
    [
        ("[warm_list.weights]\nbogus = 1\n", "Unknown warm_list weight"),
        ("[warm_list.weights]\nkeyword = -1\n", "non-negative"),
        (
            "[warm_list.weights]\nkeyword = 0\ndm_history = 0\nendorsements = 0\nconnected_recency = 0\n",
            "greater than 0",
        ),
        ("[warm_list]\nkeywords = \"saas\"\n", "list of strings"),
        ("[warm_list]\nkeyword_saturation = 0\n", "keyword_saturation"),
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, body: str, message: str) -> None:
    config = tmp_path / "bad.toml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(config)


def test_default_config_file_round_trips(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "config.toml")
    assert path.read_text(encoding="utf-8") == DEFAULT_CONFIG_TOML
    parsed = load_config(path).warm_list
    defaults = load_config(tmp_path / "missing.toml").warm_list
    assert parsed == defaults
    with pytest.raises(FileExistsError):
        write_default_config(path)


@pytest.mark.parametrize(
    "keyword, text, expected",
    [
        ("founder", "co-founder & ceo", True),
        ("partnership", "head of partnerships", True),
        ("ceo", "lyceo academy", False),
        ("amazon", "amazonseller tools", True),
        ("saas", "fake saas", True),
        ("代表", "代表取締役", True),
    ],
)
def test_keyword_pattern_boundaries(keyword: str, text: str, expected: bool) -> None:
    assert (keyword_pattern(keyword).search(text) is not None) is expected
