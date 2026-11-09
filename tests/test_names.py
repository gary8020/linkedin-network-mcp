from __future__ import annotations

from pathlib import Path

from linkedin_mcp.ingest import ingest_export
from linkedin_mcp.ingest.csv_utils import name_search_variants
from linkedin_mcp.server import LinkedInQueries
from conftest import FIXTURE_NOW


def names(people) -> list[str]:
    return [person.full_name for person in people]


def test_connections_name_is_canonical_over_message_display_name(queries: LinkedInQueries) -> None:
    yamada = queries.find_person("taro-yamada", limit=1)[0]
    assert yamada.full_name == "太郎 山田"
    assert queries.get_person(yamada.id).source == "connection"


def test_both_name_orders_and_compact_forms_match(queries: LinkedInQueries) -> None:
    for query in ["太郎 山田", "山田 太郎", "山田太郎", "太郎山田", "山田　太郎", "山田"]:
        assert names(queries.find_person(query)) == ["太郎 山田"], query


def test_western_names_match_in_either_order(queries: LinkedInQueries) -> None:
    assert names(queries.find_person("Sato Hiro")) == ["Hiro Sato"]
    assert names(queries.find_person("hirosato")) == ["Hiro Sato"]


def test_exact_name_ranks_first(queries: LinkedInQueries) -> None:
    assert names(queries.find_person("agency"))[0] == "Alice Agency"


def test_like_wildcards_in_names_are_literal(queries: LinkedInQueries) -> None:
    assert queries.find_person("%") == []


def test_message_name_resolves_to_same_person(queries: LinkedInQueries) -> None:
    yamada = queries.find_person("山田 太郎", limit=1)[0]
    conversations = queries.list_conversations(yamada.id)
    assert len(conversations) == 1
    summary = queries.summarize_relationship(yamada.id)
    assert (summary.messages_in, summary.messages_out) == (1, 1)


def test_message_only_name_still_updates_without_connection(export_copy: Path, tmp_path: Path) -> None:
    connections = export_copy / "Connections.csv"
    lines = [line for line in connections.read_text(encoding="utf-8").splitlines() if "taro-yamada" not in line]
    connections.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db_path = tmp_path / "db.sqlite"
    ingest_export(export_copy, db_path, now=FIXTURE_NOW)
    person = LinkedInQueries(db_path).find_person("taro-yamada", limit=1)[0]
    assert person.full_name == "山田 太郎"


def test_name_search_variants() -> None:
    assert name_search_variants("太郎 山田") == {"太郎 山田", "山田 太郎"}
    assert name_search_variants("ＡＢＣ  Def") == {"abc def", "def abc"}
    assert name_search_variants("Mary Ann Lee") == {"mary ann lee"}
    assert name_search_variants("  ") == set()
