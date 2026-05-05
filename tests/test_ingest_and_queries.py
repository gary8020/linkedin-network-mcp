from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from linkedin_mcp.ingest import ingest_export
from linkedin_mcp.server import LinkedInQueries


def test_ingest_and_query_tools(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    write_fixture(export)
    db_path = tmp_path / "linkedin.db"

    report = ingest_export(
        export,
        db_path,
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )

    assert report.people >= 2
    assert report.messages == 2

    queries = LinkedInQueries(db_path)
    alex = queries.find_person("Alex Example", limit=1)[0]
    assert alex.current_company == "Acme"
    assert alex.tie_strength > 0

    hits = queries.search_messages("roadmap", person_id=alex.id)
    assert len(hits) == 1
    assert hits[0].sender_is_me is True

    conversations = queries.list_conversations(alex.id)
    assert len(conversations) == 1
    assert conversations[0].unanswered_outbound is True

    messages = queries.get_conversation(conversations[0].conversation_id)
    assert [message.sender_is_me for message in messages] == [False, True]

    summary = queries.summarize_relationship(alex.id)
    assert summary.messages_in == 1
    assert summary.messages_out == 1
    assert summary.unanswered_outbound is True

    dormant = queries.dormant_connections(
        min_silent_days=180,
        min_active_tie_strength=0.1,
    )
    assert [tie.person.full_name for tie in dormant] == ["Alex Example"]
    assert dormant[0].last_topic == "Roadmap"


def write_fixture(root: Path) -> None:
    (root / "Profile.csv").write_text(
        "\n".join(
            [
                "First Name,Last Name,Maiden Name,Address,Birth Date,Headline,Summary,Industry,Zip Code,Geo Location,Twitter Handles,Websites,Instant Messengers",
                "Pat,Owner,,,,Builder,,Software,,Boston,,,",
            ]
        ),
        encoding="utf-8",
    )
    (root / "Connections.csv").write_text(
        "\n".join(
            [
                "Notes:",
                '"Connections exports can include a preamble."',
                "",
                "First Name,Last Name,URL,Email Address,Company,Position,Connected On",
                "Alex,Example,https://www.linkedin.com/in/alex-example,,Acme,Engineering Manager,01 Jan 2024",
                "Blair,Nochat,https://www.linkedin.com/in/blair-nochat,,Beta,Designer,01 Jan 2024",
            ]
        ),
        encoding="utf-8",
    )
    (root / "messages.csv").write_text(
        "\n".join(
            [
                '"CONVERSATION ID","CONVERSATION TITLE","FROM","SENDER PROFILE URL","TO","RECIPIENT PROFILE URLS","DATE","SUBJECT","CONTENT","FOLDER","ATTACHMENTS","IS MESSAGE DRAFT"',
                '"c1","Alex Example","Alex Example","https://www.linkedin.com/in/alex-example","Pat Owner","https://www.linkedin.com/in/pat-owner","2025-01-01 10:00:00 UTC","Intro","Great to meet you.","INBOX","","false"',
                '"c1","Alex Example","Pat Owner","https://www.linkedin.com/in/pat-owner","Alex Example","https://www.linkedin.com/in/alex-example","2025-01-02 10:00:00 UTC","Roadmap","Following up about the roadmap.","INBOX","","false"',
            ]
        ),
        encoding="utf-8",
    )
    (root / "Endorsement_Given_Info.csv").write_text(
        "\n".join(
            [
                "Endorsement Date,Skill Name,Endorsee First Name,Endorsee Last Name,Endorsee Public Url,Endorsement Status",
                "2024/01/01 09:00:00 UTC,Leadership,Alex,Example,www.linkedin.com/in/alex-example,ACCEPTED",
            ]
        ),
        encoding="utf-8",
    )
