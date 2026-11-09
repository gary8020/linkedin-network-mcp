from __future__ import annotations

from datetime import datetime

from linkedin_mcp.server import LinkedInQueries


def test_dormant_ties_include_last_inbound_message(queries: LinkedInQueries) -> None:
    ties = {tie.person.full_name: tie for tie in queries.dormant_connections()}
    assert set(ties) == {"Alice Agency", "太郎 山田", "Hiro Sato"}

    alice = ties["Alice Agency"]
    assert alice.last_topic == "Yes, sending the sponsorship deck now."
    assert alice.last_topic_is_mine is True
    assert alice.last_inbound_snippet == "Interested in sponsoring the summit booth?"
    assert alice.last_inbound_at == datetime(2024, 1, 10, 10, 0, 0)
    assert alice.outbound_unanswered is True

    yamada = ties["太郎 山田"]
    assert yamada.last_inbound_snippet == "スポンサーシップの件でご相談です"


def test_dormant_tie_without_any_inbound(queries: LinkedInQueries) -> None:
    ties = {tie.person.full_name: tie for tie in queries.dormant_connections()}
    hiro = ties["Hiro Sato"]
    assert hiro.last_topic_is_mine is True
    assert hiro.last_inbound_at is None
    assert hiro.last_inbound_snippet is None
    assert hiro.outbound_unanswered is True


def test_recent_conversation_is_not_dormant(queries: LinkedInQueries) -> None:
    names = {tie.person.full_name for tie in queries.dormant_connections(min_active_tie_strength=0.0)}
    assert "Bob Saas" not in names
