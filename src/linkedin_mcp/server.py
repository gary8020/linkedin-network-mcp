from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP

from linkedin_mcp.db import DEFAULT_DB_PATH, connect
from linkedin_mcp.ingest.csv_utils import full_name, parse_datetime, to_db_timestamp
from linkedin_mcp.models import (
    Conversation,
    DoctorReport,
    DormantTie,
    Message,
    MessageHit,
    Person,
    PersonDetail,
    RelationshipSignals,
)


class LinkedInQueries:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path).expanduser()

    def find_person(self, query: str, limit: int = 10) -> list[Person]:
        limit = clamp_limit(limit, 1, 100)
        like = f"%{query.strip().lower()}%"
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT p.*, t.score, t.active_score, t.last_message_at, t.last_touch_at,
                       t.days_since_last_message
                FROM people p
                LEFT JOIN tie_strength t ON t.person_id = p.id
                WHERE lower(p.full_name) LIKE ?
                   OR lower(coalesce(p.current_company, '')) LIKE ?
                   OR lower(coalesce(p.current_title, '')) LIKE ?
                   OR lower(coalesce(p.public_id, '')) LIKE ?
                ORDER BY
                  CASE WHEN lower(p.full_name) = ? THEN 0 ELSE 1 END,
                  coalesce(t.score, 0) DESC,
                  p.full_name ASC
                LIMIT ?
                """,
                (like, like, like, like, query.strip().lower(), limit),
            ).fetchall()
            return [person_from_row(row) for row in rows]

    def get_person(self, person_id: int) -> PersonDetail:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT p.*, t.score, t.active_score, t.last_message_at, t.last_touch_at,
                       t.days_since_last_message, t.signals
                FROM people p
                LEFT JOIN tie_strength t ON t.person_id = p.id
                WHERE p.id = ?
                """,
                (person_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"No person found with id {person_id}")
            return person_detail_from_row(row)

    def list_connections(
        self,
        company: str | None = None,
        title_contains: str | None = None,
        sort: Literal["tie_strength", "connected_on", "last_message", "last_touch"] = "tie_strength",
        limit: int = 50,
    ) -> list[Person]:
        limit = clamp_limit(limit, 1, 200)
        where = ["p.source = 'connection'"]
        params: list[object] = []
        if company:
            where.append("lower(coalesce(p.current_company, '')) LIKE ?")
            params.append(f"%{company.lower()}%")
        if title_contains:
            where.append("lower(coalesce(p.current_title, '')) LIKE ?")
            params.append(f"%{title_contains.lower()}%")
        order_by = {
            "tie_strength": "coalesce(t.score, 0) DESC, p.full_name ASC",
            "connected_on": "p.connected_on DESC, p.full_name ASC",
            "last_message": "t.last_message_at DESC NULLS LAST, p.full_name ASC",
            "last_touch": "t.last_touch_at DESC NULLS LAST, p.full_name ASC",
        }[sort]
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT p.*, t.score, t.active_score, t.last_message_at, t.last_touch_at,
                       t.days_since_last_message
                FROM people p
                LEFT JOIN tie_strength t ON t.person_id = p.id
                WHERE {' AND '.join(where)}
                ORDER BY {order_by}
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [person_from_row(row) for row in rows]

    def search_messages(
        self,
        query: str,
        person_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
        advanced_fts: bool = False,
        limit: int = 20,
    ) -> list[MessageHit]:
        limit = clamp_limit(limit, 1, 100)
        fts_query = query if advanced_fts else plain_text_to_fts(query)
        if not fts_query:
            return []

        conditions = ["messages_fts MATCH ?"]
        params: list[object] = [fts_query]
        add_message_filters(conditions, params, person_id, since, until)
        params.append(limit)
        sql = f"""
            SELECT m.*, bm25(messages_fts) AS rank
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            WHERE {' AND '.join(conditions)}
            ORDER BY rank ASC, m.sent_at DESC
            LIMIT ?
        """
        with self.connection() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = self.search_messages_like(conn, query, person_id, since, until, limit)
            return [message_hit_from_row(conn, row) for row in rows]

    def search_messages_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        person_id: int | None,
        since: str | None,
        until: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        conditions = ["lower(coalesce(m.content, '')) LIKE ?"]
        params: list[object] = [f"%{query.lower()}%"]
        add_message_filters(conditions, params, person_id, since, until, alias="m")
        params.append(limit)
        return conn.execute(
            f"""
            SELECT m.*, NULL AS rank
            FROM messages m
            WHERE {' AND '.join(conditions)}
            ORDER BY m.sent_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    def list_conversations(self, person_id: int, limit: int = 20) -> list[Conversation]:
        limit = clamp_limit(limit, 1, 100)
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT m.conversation_id,
                       max(NULLIF(m.conversation_title, '')) AS title,
                       max(m.sent_at) AS last_message_at,
                       count(*) AS message_count
                FROM messages m
                WHERE EXISTS (
                  SELECT 1
                  FROM message_participants mp
                  WHERE mp.message_id = m.id AND mp.person_id = ?
                )
                GROUP BY m.conversation_id
                ORDER BY last_message_at DESC
                LIMIT ?
                """,
                (person_id, limit),
            ).fetchall()
            return [conversation_from_row(conn, row) for row in rows]

    def get_conversation(self, conversation_id: str, limit: int = 100) -> list[Message]:
        limit = clamp_limit(limit, 1, 500)
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM messages
                WHERE conversation_id = ?
                ORDER BY sent_at DESC, id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            return [message_from_row(conn, row) for row in reversed(rows)]

    def summarize_relationship(self, person_id: int) -> RelationshipSignals:
        with self.connection() as conn:
            person = person_from_row(required_person_row(conn, person_id))
            conversation_count = conn.execute(
                """
                SELECT count(DISTINCT conversation_id)
                FROM conversation_participants
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()[0]
            message_stats = conn.execute(
                """
                SELECT min(occurred_at) AS first_message_at,
                       max(occurred_at) AS last_message_at,
                       sum(CASE WHEN type = 'msg_received' THEN 1 ELSE 0 END) AS messages_in,
                       sum(CASE WHEN type = 'msg_sent' THEN 1 ELSE 0 END) AS messages_out
                FROM interactions
                WHERE person_id = ? AND type IN ('msg_sent', 'msg_received')
                """,
                (person_id,),
            ).fetchone()
            touch = conn.execute(
                "SELECT last_touch_at FROM tie_strength WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            subjects = [
                row["subject"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT m.subject
                    FROM messages m
                    WHERE m.subject IS NOT NULL
                      AND trim(m.subject) != ''
                      AND EXISTS (
                        SELECT 1 FROM message_participants mp
                        WHERE mp.message_id = m.id AND mp.person_id = ?
                      )
                    ORDER BY m.sent_at DESC
                    LIMIT 3
                    """,
                    (person_id,),
                ).fetchall()
            ]
            return RelationshipSignals(
                person=person,
                conversation_count=int(conversation_count),
                first_message_at=message_stats["first_message_at"],
                last_message_at=message_stats["last_message_at"],
                last_touch_at=touch["last_touch_at"] if touch else None,
                messages_in=int(message_stats["messages_in"] or 0),
                messages_out=int(message_stats["messages_out"] or 0),
                last_three_subjects=subjects,
                unanswered_outbound=has_unanswered_outbound(conn, person_id),
            )

    def dormant_connections(
        self,
        min_silent_days: int = 180,
        min_active_tie_strength: float = 0.3,
        limit: int = 30,
    ) -> list[DormantTie]:
        limit = clamp_limit(limit, 1, 100)
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT p.*, t.score, t.active_score, t.last_message_at, t.last_touch_at,
                       t.days_since_last_message
                FROM people p
                JOIN tie_strength t ON t.person_id = p.id
                WHERE p.source = 'connection'
                  AND t.last_message_at IS NOT NULL
                  AND t.days_since_last_message >= ?
                  AND t.active_score >= ?
                ORDER BY t.active_score DESC, t.days_since_last_message DESC, p.full_name ASC
                LIMIT ?
                """,
                (min_silent_days, min_active_tie_strength, limit),
            ).fetchall()
            return [
                DormantTie(
                    person=person_from_row(row),
                    last_topic=last_topic(conn, int(row["id"])),
                    silent_for_days=int(row["days_since_last_message"]),
                    active_tie_strength=float(row["active_score"] or 0),
                    outbound_unanswered=has_unanswered_outbound(conn, int(row["id"])),
                )
                for row in rows
            ]

    def doctor(self, *, redact_preview: bool = False) -> DoctorReport:
        with self.connection() as conn:
            schema = conn.execute(
                "SELECT value FROM schema_info WHERE key = 'schema_version'"
            ).fetchone()
            preview = None
            if redact_preview:
                preview_people = [
                    redact_person(person.model_dump(mode="json"))
                    for person in self.find_person("", limit=3)
                ]
                preview = {"find_person_sample": preview_people}
            return DoctorReport(
                db_path=str(self.db_path),
                schema_version=schema["value"] if schema else None,
                source_rows=count_rows(conn, "source_rows"),
                people=count_rows(conn, "people"),
                connections=conn.execute("SELECT count(*) FROM people WHERE source = 'connection'").fetchone()[0],
                message_only_people=conn.execute(
                    "SELECT count(*) FROM people WHERE source = 'message_only'"
                ).fetchone()[0],
                messages=count_rows(conn, "messages"),
                conversations=conn.execute("SELECT count(DISTINCT conversation_id) FROM messages").fetchone()[0],
                unresolved_message_senders=conn.execute(
                    "SELECT count(*) FROM messages WHERE sender_is_me = 0 AND sender_id IS NULL"
                ).fetchone()[0],
                low_confidence_merges=conn.execute(
                    "SELECT count(*) FROM people WHERE merge_confidence < 1.0"
                ).fetchone()[0],
                preview=preview,
            )

    def connection(self) -> sqlite3.Connection:
        return connect(self.db_path, read_only=True)


def create_mcp(db_path: str | Path = DEFAULT_DB_PATH) -> FastMCP:
    queries = LinkedInQueries(db_path)
    mcp = FastMCP(
        "linkedin-mcp",
        instructions="Read-only tools for querying a local LinkedIn export SQLite database.",
    )

    @mcp.tool
    def find_person(query: str, limit: int = 10) -> list[Person]:
        """Search people by name, company, title, or public LinkedIn ID."""
        return queries.find_person(query, limit)

    @mcp.tool
    def get_person(person_id: int) -> PersonDetail:
        """Return one person with provenance-friendly relationship signals."""
        return queries.get_person(person_id)

    @mcp.tool
    def list_connections(
        company: str | None = None,
        title_contains: str | None = None,
        sort: Literal["tie_strength", "connected_on", "last_message", "last_touch"] = "tie_strength",
        limit: int = 50,
    ) -> list[Person]:
        """List first-degree connections, optionally filtered by company or title."""
        return queries.list_connections(company, title_contains, sort, limit)

    @mcp.tool
    def search_messages(
        query: str,
        person_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
        advanced_fts: bool = False,
        limit: int = 20,
    ) -> list[MessageHit]:
        """Search LinkedIn DMs. Plain text is escaped unless advanced_fts is true."""
        return queries.search_messages(query, person_id, since, until, advanced_fts, limit)

    @mcp.tool
    def list_conversations(person_id: int, limit: int = 20) -> list[Conversation]:
        """List conversations involving a person."""
        return queries.list_conversations(person_id, limit)

    @mcp.tool
    def get_conversation(conversation_id: str, limit: int = 100) -> list[Message]:
        """Return messages in a conversation, chronological order."""
        return queries.get_conversation(conversation_id, limit)

    @mcp.tool
    def summarize_relationship(person_id: int) -> RelationshipSignals:
        """Return aggregated relationship counts and dates for one person."""
        return queries.summarize_relationship(person_id)

    @mcp.tool
    def dormant_connections(
        min_silent_days: int = 180,
        min_active_tie_strength: float = 0.3,
        limit: int = 30,
    ) -> list[DormantTie]:
        """List first-degree connections with strong past ties and stale DM activity."""
        return queries.dormant_connections(min_silent_days, min_active_tie_strength, limit)

    return mcp


def run_server(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    create_mcp(db_path).run(transport="stdio")


def person_from_row(row: sqlite3.Row) -> Person:
    return Person(
        id=int(row["id"]),
        full_name=row["full_name"],
        profile_url=row["profile_url"],
        current_company=row["current_company"],
        current_title=row["current_title"],
        tie_strength=float(row["score"] or 0),
        active_tie_strength=float(row["active_score"] or 0),
        last_message_at=row["last_message_at"],
        last_touch_at=row["last_touch_at"],
        days_since_last_message=row["days_since_last_message"],
    )


def person_detail_from_row(row: sqlite3.Row) -> PersonDetail:
    signals = json.loads(row["signals"]) if row["signals"] else {}
    return PersonDetail(
        **person_from_row(row).model_dump(),
        public_id=row["public_id"],
        location=row["location"],
        connected_on=row["connected_on"],
        source=row["source"],
        merge_confidence=float(row["merge_confidence"]),
        signals=signals,
    )


def required_person_row(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT p.*, t.score, t.active_score, t.last_message_at, t.last_touch_at,
               t.days_since_last_message
        FROM people p
        LEFT JOIN tie_strength t ON t.person_id = p.id
        WHERE p.id = ?
        """,
        (person_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No person found with id {person_id}")
    return row


def message_hit_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> MessageHit:
    return MessageHit(
        id=int(row["id"]),
        conversation_id=row["conversation_id"],
        conversation_title=row["conversation_title"],
        sender_id=row["sender_id"],
        sender_name=sender_name(conn, row),
        sender_is_me=bool(row["sender_is_me"]),
        sent_at=row["sent_at"],
        subject=row["subject"],
        snippet=snippet(row["content"] or ""),
        folder=row["folder"],
        rank=row["rank"],
    )


def message_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Message:
    recipients = [
        item["display_name"]
        for item in conn.execute(
            """
            SELECT display_name
            FROM message_participants
            WHERE message_id = ? AND role = 'recipient' AND display_name IS NOT NULL
            ORDER BY id
            """,
            (row["id"],),
        ).fetchall()
    ]
    return Message(
        id=int(row["id"]),
        conversation_id=row["conversation_id"],
        conversation_title=row["conversation_title"],
        sender_id=row["sender_id"],
        sender_name=sender_name(conn, row),
        sender_is_me=bool(row["sender_is_me"]),
        sent_at=row["sent_at"],
        subject=row["subject"],
        content=row["content"],
        folder=row["folder"],
        is_draft=bool(row["is_draft"]) if row["is_draft"] is not None else None,
        recipient_names=recipients,
    )


def conversation_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Conversation:
    names = [
        item["display_name"]
        for item in conn.execute(
            """
            SELECT DISTINCT display_name
            FROM conversation_participants
            WHERE conversation_id = ? AND is_me = 0 AND display_name IS NOT NULL
            ORDER BY display_name
            """,
            (row["conversation_id"],),
        ).fetchall()
    ]
    unanswered = conn.execute(
        """
        SELECT 1 FROM unanswered_threads
        WHERE conversation_id = ? AND is_group = 0
        """,
        (row["conversation_id"],),
    ).fetchone()
    return Conversation(
        conversation_id=row["conversation_id"],
        title=row["title"],
        participant_names=names,
        last_message_at=row["last_message_at"],
        message_count=int(row["message_count"]),
        is_group=len(names) > 1,
        unanswered_outbound=unanswered is not None,
    )


def sender_name(conn: sqlite3.Connection, message_row: sqlite3.Row) -> str | None:
    if bool(message_row["sender_is_me"]):
        return self_name(conn)
    row = conn.execute(
        """
        SELECT display_name
        FROM message_participants
        WHERE message_id = ? AND role = 'sender'
        LIMIT 1
        """,
        (message_row["id"],),
    ).fetchone()
    return row["display_name"] if row else None


def self_name(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT first_name, last_name FROM self_profile WHERE id = 1").fetchone()
    if row is None:
        return None
    return full_name(row["first_name"], row["last_name"]) or None


def plain_text_to_fts(query: str) -> str:
    tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens)


def add_message_filters(
    conditions: list[str],
    params: list[object],
    person_id: int | None,
    since: str | None,
    until: str | None,
    *,
    alias: str = "m",
) -> None:
    if person_id is not None:
        conditions.append(
            f"""
            EXISTS (
              SELECT 1
              FROM message_participants mp
              WHERE mp.message_id = {alias}.id AND mp.person_id = ?
            )
            """
        )
        params.append(person_id)
    since_ts = tool_time_to_db(since)
    if since_ts:
        conditions.append(f"{alias}.sent_at >= ?")
        params.append(since_ts)
    until_ts = tool_time_to_db(until)
    if until_ts:
        conditions.append(f"{alias}.sent_at <= ?")
        params.append(until_ts)


def tool_time_to_db(value: str | None) -> str | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is not None:
        return to_db_timestamp(parsed)
    try:
        return to_db_timestamp(parse_datetime(value + " 00:00:00"))
    except Exception:
        return value


def snippet(content: str, max_length: int = 280) -> str:
    collapsed = re.sub(r"\s+", " ", content).strip()
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max_length - 1].rstrip() + "..."


def last_topic(conn: sqlite3.Connection, person_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT m.subject, m.content
        FROM messages m
        WHERE EXISTS (
          SELECT 1 FROM message_participants mp
          WHERE mp.message_id = m.id AND mp.person_id = ?
        )
        ORDER BY m.sent_at DESC, m.id DESC
        LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    if row is None:
        return None
    if row["subject"]:
        return row["subject"]
    for line in (row["content"] or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return snippet(cleaned, 120)
    return None


def has_unanswered_outbound(conn: sqlite3.Connection, person_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM unanswered_threads ut
        JOIN conversation_participants cp ON cp.conversation_id = ut.conversation_id
        WHERE cp.person_id = ? AND ut.is_group = 0
        LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    return row is not None


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def redact_person(data: dict[str, object]) -> dict[str, object]:
    redacted = dict(data)
    if isinstance(redacted.get("full_name"), str):
        redacted["full_name"] = mask_text(redacted["full_name"])
    if isinstance(redacted.get("profile_url"), str):
        redacted["profile_url"] = "https://www.linkedin.com/in/<redacted>"
    if isinstance(redacted.get("current_company"), str):
        redacted["current_company"] = mask_text(redacted["current_company"])
    if isinstance(redacted.get("current_title"), str):
        redacted["current_title"] = mask_text(redacted["current_title"])
    return redacted


def mask_text(value: object) -> str:
    text = str(value)
    if len(text) <= 2:
        return "*" * len(text)
    return text[0] + "*" * (len(text) - 2) + text[-1]


def clamp_limit(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
