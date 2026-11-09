from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from linkedin_mcp.db import DEFAULT_DB_PATH, connect, reset_schema
from linkedin_mcp.ingest.csv_utils import (
    CsvRecord,
    ExportSource,
    date_diff_days,
    full_name,
    is_system_sender,
    normalize_name,
    normalize_profile_url,
    parse_date,
    parse_datetime,
    parse_person_list,
    parse_profile_url_list,
    read_linkedin_csv,
    row_hash,
    to_db_timestamp,
    truthy,
)
from linkedin_mcp.models import IngestReport


MESSAGE_REQUIRED_COLUMNS = (
    "CONVERSATION ID",
    "FROM",
    "SENDER PROFILE URL",
    "TO",
    "RECIPIENT PROFILE URLS",
    "DATE",
    "CONTENT",
)


def ingest_export(
    export_path: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    include_sensitive: bool = False,
    now: datetime | None = None,
) -> IngestReport:
    del include_sensitive
    ref_now = now or datetime.now(timezone.utc)
    with ExportSource(export_path) as source:
        if not source.exists("Connections.csv"):
            raise FileNotFoundError(
                f"No Connections.csv found under {export_path}. Pass the top-level export directory or ZIP."
            )
        conn = connect(db_path)
        try:
            reset_schema(conn)
            ctx = IngestContext(conn, source, Path(export_path), ref_now)
            ctx.ingest_self_identity()
            ctx.discover_self_urls()
            ctx.ingest_career_mirrors()
            ctx.ingest_connections()
            ctx.ingest_invitations()
            ctx.ingest_endorsements()
            ctx.ingest_messages()
            ctx.compute_unanswered_threads()
            ctx.compute_tie_strength()
            conn.commit()
            return ctx.report(Path(db_path).expanduser())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class IngestContext:
    def __init__(self, conn: sqlite3.Connection, source: ExportSource, source_path: Path, now: datetime):
        self.conn = conn
        self.source = source
        self.source_path = source_path
        self.now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        self.warnings: list[str] = []
        self.self_names: set[str] = set()
        self.self_url_keys: set[str] = set()
        self.self_urls: set[str] = set()

    def report(self, db_path: Path) -> IngestReport:
        stats = {
            "source_rows": self.conn.execute("SELECT count(*) FROM source_rows").fetchone()[0],
            "people": self.conn.execute("SELECT count(*) FROM people").fetchone()[0],
            "messages": self.conn.execute("SELECT count(*) FROM messages").fetchone()[0],
            "conversations": self.conn.execute("SELECT count(DISTINCT conversation_id) FROM messages").fetchone()[0],
        }
        return IngestReport(
            db_path=str(db_path),
            source_path=str(self.source_path),
            warnings=self.warnings,
            **stats,
        )

    def ingest_self_identity(self) -> None:
        rows = read_linkedin_csv(
            self.source,
            "Profile.csv",
            required_columns=("First Name", "Last Name"),
        )
        if rows:
            record = rows[0]
            raw_row_id = self.record_source_row(record)
            data = record.data
            self.conn.execute(
                """
                INSERT INTO self_profile(
                  id, first_name, last_name, headline, summary, industry, location, raw_row_id
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value_or_none(data.get("First Name")),
                    value_or_none(data.get("Last Name")),
                    value_or_none(data.get("Headline")),
                    value_or_none(data.get("Summary")),
                    value_or_none(data.get("Industry")),
                    value_or_none(data.get("Geo Location") or data.get("Location")),
                    raw_row_id,
                ),
            )
            owner_name = full_name(data.get("First Name"), data.get("Last Name"))
            if owner_name:
                self.self_names.add(normalize_name(owner_name))

        self.ingest_contact_methods("Email Addresses.csv", "email", "Email Address", "Primary")
        self.ingest_contact_methods("PhoneNumbers.csv", "phone", "Number", None)

    def ingest_contact_methods(
        self,
        file_path: str,
        method_type: str,
        value_column: str,
        primary_column: str | None,
    ) -> None:
        rows = read_linkedin_csv(self.source, file_path, required_columns=(value_column,))
        for record in rows:
            value = value_or_none(record.data.get(value_column))
            if value is None:
                continue
            raw_row_id = self.record_source_row(record)
            is_primary = truthy(record.data.get(primary_column)) if primary_column else None
            self.conn.execute(
                """
                INSERT INTO self_contact_methods(type, value, is_primary, raw_row_id)
                VALUES (?, ?, ?, ?)
                """,
                (method_type, value, is_primary, raw_row_id),
            )

    def discover_self_urls(self) -> None:
        message_rows = read_linkedin_csv(
            self.source,
            "messages.csv",
            required_columns=MESSAGE_REQUIRED_COLUMNS,
        )
        for record in message_rows:
            data = record.data
            if self.is_self_name(data.get("FROM")):
                self.add_self_url(data.get("SENDER PROFILE URL"))
            names = parse_person_list(data.get("TO"))
            urls = parse_profile_url_list(data.get("RECIPIENT PROFILE URLS"))
            if len(names) == 1 and self.is_self_name(names[0]) and len(urls) == 1:
                self.add_self_url(urls[0])

        invitation_rows = read_linkedin_csv(
            self.source,
            "Invitations.csv",
            required_columns=("From", "To", "Sent At"),
        )
        for record in invitation_rows:
            data = record.data
            if self.is_self_name(data.get("From")):
                self.add_self_url(data.get("inviterProfileUrl"))
            if self.is_self_name(data.get("To")):
                self.add_self_url(data.get("inviteeProfileUrl"))

    def add_self_url(self, value: str | None) -> None:
        normalized = normalize_profile_url(value)
        if normalized is None:
            return
        self.self_url_keys.add(normalized.key)
        self.self_urls.add(normalized.url)

    def ingest_career_mirrors(self) -> None:
        self.ingest_positions()
        self.ingest_skills()
        self.ingest_projects()
        self.ingest_education()

    def ingest_positions(self) -> None:
        rows = read_linkedin_csv(self.source, "Positions.csv", required_columns=("Company Name", "Title"))
        for record in rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            data = record.data
            self.conn.execute(
                """
                INSERT INTO positions(
                  source_row_id, company_name, title, description, location,
                  started_on, finished_on, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    value_or_none(data.get("Company Name")),
                    value_or_none(data.get("Title")),
                    value_or_none(data.get("Description")),
                    value_or_none(data.get("Location")),
                    value_or_none(data.get("Started On")),
                    value_or_none(data.get("Finished On")),
                    raw_json,
                ),
            )

    def ingest_skills(self) -> None:
        rows = read_linkedin_csv(self.source, "Skills.csv", required_columns=("Name",))
        for record in rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            self.conn.execute(
                "INSERT INTO skills(source_row_id, name, raw_json) VALUES (?, ?, ?)",
                (raw_row_id, value_or_none(record.data.get("Name")), raw_json),
            )

    def ingest_projects(self) -> None:
        rows = read_linkedin_csv(self.source, "Projects.csv", required_columns=("Title",))
        for record in rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            data = record.data
            self.conn.execute(
                """
                INSERT INTO projects(source_row_id, title, description, url, started_on, finished_on, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    value_or_none(data.get("Title")),
                    value_or_none(data.get("Description")),
                    value_or_none(data.get("Url") or data.get("URL")),
                    value_or_none(data.get("Started On")),
                    value_or_none(data.get("Finished On")),
                    raw_json,
                ),
            )

    def ingest_education(self) -> None:
        rows = read_linkedin_csv(self.source, "Education.csv", required_columns=("School Name",))
        for record in rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            data = record.data
            self.conn.execute(
                """
                INSERT INTO education(
                  source_row_id, school_name, start_date, end_date, notes,
                  degree_name, activities, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    value_or_none(data.get("School Name")),
                    value_or_none(data.get("Start Date")),
                    value_or_none(data.get("End Date")),
                    value_or_none(data.get("Notes")),
                    value_or_none(data.get("Degree Name")),
                    value_or_none(data.get("Activities")),
                    raw_json,
                ),
            )

    def ingest_connections(self) -> None:
        rows = read_linkedin_csv(
            self.source,
            "Connections.csv",
            required_columns=("First Name", "Last Name", "URL"),
        )
        for record in rows:
            raw_row_id = self.record_source_row(record)
            data = record.data
            name = full_name(data.get("First Name"), data.get("Last Name"))
            connected_on = parse_date(data.get("Connected On"))
            self.upsert_person(
                name,
                profile_url=data.get("URL"),
                company=data.get("Company"),
                title=data.get("Position"),
                connected_on=connected_on,
                source="connection",
                raw_row_id=raw_row_id,
                confidence=1.0,
            )

    def ingest_invitations(self) -> None:
        rows = read_linkedin_csv(
            self.source,
            "Invitations.csv",
            required_columns=("From", "To", "Sent At"),
        )
        for record in rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            data = record.data
            self.conn.execute(
                """
                INSERT INTO invitations(
                  source_row_id, from_name, to_name, sent_at, message, direction,
                  inviter_profile_url, invitee_profile_url, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    value_or_none(data.get("From")),
                    value_or_none(data.get("To")),
                    to_db_timestamp(parse_datetime(data.get("Sent At"))),
                    value_or_none(data.get("Message")),
                    value_or_none(data.get("Direction")),
                    normalized_url_or_none(data.get("inviterProfileUrl")),
                    normalized_url_or_none(data.get("inviteeProfileUrl")),
                    raw_json,
                ),
            )

    def ingest_endorsements(self) -> None:
        given_rows = read_linkedin_csv(
            self.source,
            "Endorsement_Given_Info.csv",
            required_columns=("Endorsement Date", "Skill Name", "Endorsee First Name", "Endorsee Last Name"),
        )
        for record in given_rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            data = record.data
            occurred_at = parse_datetime(data.get("Endorsement Date"))
            person_id = self.upsert_person(
                full_name(data.get("Endorsee First Name"), data.get("Endorsee Last Name")),
                profile_url=data.get("Endorsee Public Url"),
                source="endorsement_only",
                raw_row_id=raw_row_id,
                confidence=1.0,
            )
            self.conn.execute(
                """
                INSERT INTO endorsements_given(
                  source_row_id, endorsement_date, skill_name, endorsee_first_name,
                  endorsee_last_name, endorsee_profile_url, status, person_id, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    to_db_timestamp(occurred_at),
                    value_or_none(data.get("Skill Name")),
                    value_or_none(data.get("Endorsee First Name")),
                    value_or_none(data.get("Endorsee Last Name")),
                    normalized_url_or_none(data.get("Endorsee Public Url")),
                    value_or_none(data.get("Endorsement Status")),
                    person_id,
                    raw_json,
                ),
            )
            if person_id is not None and occurred_at is not None:
                self.add_interaction(
                    person_id,
                    "endorsement_given",
                    occurred_at,
                    raw_row_id,
                    {"skill": value_or_none(data.get("Skill Name"))},
                )

        received_rows = read_linkedin_csv(
            self.source,
            "Endorsement_Received_Info.csv",
            required_columns=("Endorsement Date", "Skill Name", "Endorser First Name", "Endorser Last Name"),
        )
        for record in received_rows:
            raw_row_id, raw_json = self.record_source_row_with_json(record)
            data = record.data
            occurred_at = parse_datetime(data.get("Endorsement Date"))
            person_id = self.upsert_person(
                full_name(data.get("Endorser First Name"), data.get("Endorser Last Name")),
                profile_url=data.get("Endorser Public Url"),
                source="endorsement_only",
                raw_row_id=raw_row_id,
                confidence=1.0,
            )
            self.conn.execute(
                """
                INSERT INTO endorsements_received(
                  source_row_id, endorsement_date, skill_name, endorser_first_name,
                  endorser_last_name, endorser_profile_url, status, person_id, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    to_db_timestamp(occurred_at),
                    value_or_none(data.get("Skill Name")),
                    value_or_none(data.get("Endorser First Name")),
                    value_or_none(data.get("Endorser Last Name")),
                    normalized_url_or_none(data.get("Endorser Public Url")),
                    value_or_none(data.get("Endorsement Status")),
                    person_id,
                    raw_json,
                ),
            )
            if person_id is not None and occurred_at is not None:
                self.add_interaction(
                    person_id,
                    "endorsement_received",
                    occurred_at,
                    raw_row_id,
                    {"skill": value_or_none(data.get("Skill Name"))},
                )

    def ingest_messages(self) -> None:
        if not self.source.exists("messages.csv"):
            self.warnings.append(
                "No messages.csv found in the export (matched case-insensitively). "
                "DM search, relationship summaries, DM-based tie strength, and dormant ties will be empty. "
                "Re-request the export from LinkedIn with messages included."
            )
            return
        rows = read_linkedin_csv(
            self.source,
            "messages.csv",
            required_columns=MESSAGE_REQUIRED_COLUMNS,
        )
        for record in rows:
            raw_row_id = self.record_source_row(record)
            data = record.data
            sent_at = parse_datetime(data.get("DATE"))
            if sent_at is None:
                self.warnings.append(
                    f"messages.csv row {record.logical_row_number}: skipped message with unparseable DATE"
                )
                continue

            sender_name = value_or_none(data.get("FROM"))
            sender_url = data.get("SENDER PROFILE URL")
            sender_is_me = self.is_self(sender_name, sender_url)
            sender_id: int | None = None
            if not sender_is_me and not is_system_sender(sender_name):
                sender_id = self.upsert_person(
                    sender_name or "",
                    profile_url=sender_url,
                    source="message_only",
                    raw_row_id=raw_row_id,
                    confidence=1.0,
                )

            cur = self.conn.execute(
                """
                INSERT INTO messages(
                  source_row_id, conversation_id, conversation_title, sender_id, sender_is_me,
                  sent_at, subject, content, folder, is_draft
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_row_id,
                    data.get("CONVERSATION ID"),
                    value_or_none(data.get("CONVERSATION TITLE")),
                    sender_id,
                    sender_is_me,
                    to_db_timestamp(sent_at),
                    value_or_none(data.get("SUBJECT")),
                    value_or_none(data.get("CONTENT")),
                    value_or_none(data.get("FOLDER")),
                    truthy(data.get("IS MESSAGE DRAFT")),
                ),
            )
            message_id = int(cur.lastrowid)

            self.add_message_participant(
                message_id,
                data.get("CONVERSATION ID") or "",
                "sender",
                sender_name,
                sender_url,
                sender_id,
                sender_is_me,
            )

            recipient_people = self.resolve_recipients(record, raw_row_id)
            for recipient_name, recipient_url, person_id, is_me in recipient_people:
                self.add_message_participant(
                    message_id,
                    data.get("CONVERSATION ID") or "",
                    "recipient",
                    recipient_name,
                    recipient_url,
                    person_id,
                    is_me,
                )

            if sender_is_me:
                for person_id in sorted({item[2] for item in recipient_people if item[2] is not None}):
                    self.add_interaction(person_id, "msg_sent", sent_at, message_id, None)
            elif sender_id is not None:
                self.add_interaction(sender_id, "msg_received", sent_at, message_id, None)

    def resolve_recipients(
        self,
        record: CsvRecord,
        raw_row_id: int,
    ) -> list[tuple[str | None, str | None, int | None, bool]]:
        data = record.data
        names = parse_person_list(data.get("TO"))
        urls = parse_profile_url_list(data.get("RECIPIENT PROFILE URLS"))
        if not names and urls:
            names = [""] * len(urls)
        result: list[tuple[str | None, str | None, int | None, bool]] = []
        for index, name in enumerate(names):
            url = urls[index] if index < len(urls) else None
            is_me = self.is_self(name, url)
            person_id: int | None = None
            if not is_me and not is_system_sender(name):
                person_id = self.upsert_person(
                    name or "",
                    profile_url=url,
                    source="message_only",
                    raw_row_id=raw_row_id,
                    confidence=1.0,
                )
            result.append((value_or_none(name), normalized_url_or_none(url), person_id, is_me))
        return result

    def upsert_person(
        self,
        name: str,
        *,
        profile_url: str | None = None,
        company: str | None = None,
        title: str | None = None,
        connected_on: str | None = None,
        source: str,
        raw_row_id: int | None,
        confidence: float,
        location: str | None = None,
    ) -> int | None:
        cleaned_name = value_or_none(name)
        if cleaned_name is None or is_system_sender(cleaned_name):
            return None

        normalized_url = normalize_profile_url(profile_url)
        person_id: int | None = None
        if normalized_url is not None:
            person_id = self.find_person_by_key("profile_url", normalized_url.key)
            if person_id is None and normalized_url.public_id:
                person_id = self.find_person_by_key("public_id", normalized_url.public_id.lower())
            if person_id is None:
                row = self.conn.execute(
                    "SELECT id FROM people WHERE profile_url = ?",
                    (normalized_url.url,),
                ).fetchone()
                person_id = int(row["id"]) if row else None

        medium_key = make_name_company_key(cleaned_name, company)
        if person_id is None and normalized_url is not None and medium_key is not None:
            candidate = self.find_person_by_key("name_company", medium_key)
            if candidate is not None:
                person_id = candidate
                confidence = min(confidence, 0.85)

        if person_id is None and normalized_url is None:
            if medium_key is not None:
                person_id = self.find_person_by_key("name_company", medium_key)
            if person_id is None:
                person_id = self.find_person_by_key("message_name", normalize_name(cleaned_name))

        if person_id is None:
            cur = self.conn.execute(
                """
                INSERT INTO people(
                  full_name, public_id, profile_url, current_company, current_title,
                  location, connected_on, source, merge_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_name,
                    normalized_url.public_id if normalized_url else None,
                    normalized_url.url if normalized_url else None,
                    value_or_none(company),
                    value_or_none(title),
                    value_or_none(location),
                    connected_on,
                    source,
                    confidence,
                ),
            )
            person_id = int(cur.lastrowid)
        else:
            self.update_person(
                person_id,
                cleaned_name,
                normalized_url.url if normalized_url else None,
                normalized_url.public_id if normalized_url else None,
                company,
                title,
                connected_on,
                source,
                confidence,
                location,
            )

        if raw_row_id is not None:
            if normalized_url is not None:
                self.record_person_key(person_id, raw_row_id, "profile_url", normalized_url.key, confidence)
                if normalized_url.public_id:
                    self.record_person_key(
                        person_id,
                        raw_row_id,
                        "public_id",
                        normalized_url.public_id.lower(),
                        confidence,
                    )
            if medium_key is not None:
                self.record_person_key(person_id, raw_row_id, "name_company", medium_key, min(confidence, 0.85))
            if source == "message_only":
                self.record_person_key(person_id, raw_row_id, "message_name", normalize_name(cleaned_name), 0.6)
        return person_id

    def update_person(
        self,
        person_id: int,
        name: str,
        profile_url: str | None,
        public_id: str | None,
        company: str | None,
        title: str | None,
        connected_on: str | None,
        source: str,
        confidence: float,
        location: str | None,
    ) -> None:
        existing = self.conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        source_value = existing["source"]
        if source == "connection" and source_value != "connection":
            source_value = "connection"
        merge_confidence = min(float(existing["merge_confidence"]), confidence)
        self.conn.execute(
            """
            UPDATE people
            SET full_name = COALESCE(NULLIF(?, ''), full_name),
                public_id = COALESCE(public_id, ?),
                profile_url = COALESCE(profile_url, ?),
                current_company = CASE
                  WHEN ? IS NOT NULL AND (current_company IS NULL OR ? = 'connection') THEN ?
                  ELSE current_company
                END,
                current_title = CASE
                  WHEN ? IS NOT NULL AND (current_title IS NULL OR ? = 'connection') THEN ?
                  ELSE current_title
                END,
                location = COALESCE(location, ?),
                connected_on = COALESCE(connected_on, ?),
                source = ?,
                merge_confidence = ?
            WHERE id = ?
            """,
            (
                name,
                public_id,
                profile_url,
                value_or_none(company),
                source,
                value_or_none(company),
                value_or_none(title),
                source,
                value_or_none(title),
                value_or_none(location),
                connected_on,
                source_value,
                merge_confidence,
                person_id,
            ),
        )

    def find_person_by_key(self, key_type: str, normalized_key: str) -> int | None:
        rows = self.conn.execute(
            """
            SELECT DISTINCT person_id
            FROM person_source_keys
            WHERE key_type = ? AND normalized_key = ?
            """,
            (key_type, normalized_key),
        ).fetchall()
        if len(rows) == 1:
            return int(rows[0]["person_id"])
        return None

    def record_person_key(
        self,
        person_id: int,
        source_row_id: int,
        key_type: str,
        normalized_key: str,
        confidence: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO person_source_keys(
              person_id, source_row_id, key_type, normalized_key, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (person_id, source_row_id, key_type, normalized_key, confidence),
        )

    def add_message_participant(
        self,
        message_id: int,
        conversation_id: str,
        role: str,
        display_name: str | None,
        profile_url: str | None,
        person_id: int | None,
        is_me: bool,
    ) -> None:
        normalized_url = normalize_profile_url(profile_url)
        participant_key = "me" if is_me else (f"person:{person_id}" if person_id else f"name:{normalize_name(display_name)}")
        canonical_url = normalized_url.url if normalized_url else None
        self.conn.execute(
            """
            INSERT OR IGNORE INTO message_participants(
              message_id, participant_key, person_id, display_name, profile_url, role, is_me
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, participant_key, person_id, value_or_none(display_name), canonical_url, role, is_me),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO conversation_participants(
              conversation_id, participant_key, person_id, display_name, profile_url, is_me
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, participant_key, person_id, value_or_none(display_name), canonical_url, is_me),
        )

    def add_interaction(
        self,
        person_id: int,
        interaction_type: str,
        occurred_at: datetime,
        ref_id: int,
        metadata: dict[str, object] | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO interactions(person_id, type, occurred_at, ref_id, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                person_id,
                interaction_type,
                to_db_timestamp(occurred_at),
                ref_id,
                json.dumps(metadata, sort_keys=True) if metadata else None,
            ),
        )

    def compute_unanswered_threads(self) -> None:
        conversation_ids = [
            row["conversation_id"]
            for row in self.conn.execute("SELECT DISTINCT conversation_id FROM messages").fetchall()
        ]
        for conversation_id in conversation_ids:
            last = self.conn.execute(
                """
                SELECT id, sent_at, sender_is_me
                FROM messages
                WHERE conversation_id = ?
                ORDER BY sent_at DESC, id DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if not last or not bool(last["sender_is_me"]):
                continue
            non_me_count = self.conn.execute(
                """
                SELECT count(*)
                FROM conversation_participants
                WHERE conversation_id = ? AND is_me = 0
                """,
                (conversation_id,),
            ).fetchone()[0]
            self.conn.execute(
                """
                INSERT INTO unanswered_threads(conversation_id, last_message_id, last_sent_at, age_days, is_group)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    last["id"],
                    last["sent_at"],
                    date_diff_days(last["sent_at"], self.now) or 0,
                    non_me_count > 1,
                ),
            )

    def compute_tie_strength(self) -> None:
        for person in self.conn.execute("SELECT id, source FROM people").fetchall():
            person_id = int(person["id"])
            counts = {
                row["type"]: int(row["count"])
                for row in self.conn.execute(
                    """
                    SELECT type, count(*) AS count
                    FROM interactions
                    WHERE person_id = ?
                    GROUP BY type
                    """,
                    (person_id,),
                ).fetchall()
            }
            sent = counts.get("msg_sent", 0)
            received = counts.get("msg_received", 0)
            endorsements = counts.get("endorsement_given", 0) + counts.get("endorsement_received", 0)

            last_message_at = scalar(
                self.conn.execute(
                    """
                    SELECT max(occurred_at)
                    FROM interactions
                    WHERE person_id = ? AND type IN ('msg_sent', 'msg_received')
                    """,
                    (person_id,),
                )
            )
            last_touch_at = scalar(
                self.conn.execute(
                    "SELECT max(occurred_at) FROM interactions WHERE person_id = ?",
                    (person_id,),
                )
            )
            days_since_message = date_diff_days(last_message_at, self.now)
            days_since_touch = date_diff_days(last_touch_at, self.now)

            message_volume = min(1.0, math.log1p(sent + received) / math.log1p(50))
            first_degree = 1.0 if person["source"] == "connection" else 0.0
            endorsement_overlap = min(1.0, endorsements / 5.0)
            active_score = (
                0.60 * message_volume
                + 0.25 * first_degree
                + 0.15 * endorsement_overlap
            )
            recency = math.exp(-(days_since_message or 10_000) / 365.0) if last_message_at else 0.0
            score = 0.70 * active_score + 0.30 * recency
            signals = {
                "message_volume": round(message_volume, 4),
                "messages_sent": sent,
                "messages_received": received,
                "first_degree": first_degree,
                "endorsement_overlap": round(endorsement_overlap, 4),
                "endorsements": endorsements,
                "message_recency": round(recency, 4),
                "weights": {
                    "active_message_volume": 0.60,
                    "active_first_degree": 0.25,
                    "active_endorsement_overlap": 0.15,
                    "score_active": 0.70,
                    "score_recency": 0.30,
                },
            }
            self.conn.execute(
                """
                INSERT INTO tie_strength(
                  person_id, score, active_score, last_message_at, last_touch_at,
                  days_since_last_message, days_since_last_touch, signals
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    person_id,
                    round(score, 6),
                    round(active_score, 6),
                    last_message_at,
                    last_touch_at,
                    days_since_message,
                    days_since_touch,
                    json.dumps(signals, sort_keys=True),
                ),
            )

    def is_self_name(self, name: str | None) -> bool:
        normalized = normalize_name(name)
        return bool(normalized and normalized in self.self_names)

    def is_self(self, name: str | None, profile_url: str | None) -> bool:
        normalized_url = normalize_profile_url(profile_url)
        if normalized_url is not None and normalized_url.key in self.self_url_keys:
            return True
        return self.is_self_name(name)

    def record_source_row(self, record: CsvRecord) -> int:
        raw_hash, raw_json = row_hash(record.data)
        cur = self.conn.execute(
            """
            INSERT INTO source_rows(file_path, logical_row_number, row_hash, raw_json)
            VALUES (?, ?, ?, ?)
            """,
            (record.file_path, record.logical_row_number, raw_hash, raw_json),
        )
        return int(cur.lastrowid)

    def record_source_row_with_json(self, record: CsvRecord) -> tuple[int, str]:
        raw_hash, raw_json = row_hash(record.data)
        cur = self.conn.execute(
            """
            INSERT INTO source_rows(file_path, logical_row_number, row_hash, raw_json)
            VALUES (?, ?, ?, ?)
            """,
            (record.file_path, record.logical_row_number, raw_hash, raw_json),
        )
        return int(cur.lastrowid), raw_json


def value_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalized_url_or_none(value: str | None) -> str | None:
    normalized = normalize_profile_url(value)
    return normalized.url if normalized else None


def make_name_company_key(name: str, company: str | None) -> str | None:
    normalized_name = normalize_name(name)
    normalized_company = normalize_name(company)
    if not normalized_name or not normalized_company:
        return None
    return f"{normalized_name}|{normalized_company}"


def scalar(cursor: Iterable[sqlite3.Row]) -> str | None:
    row = next(iter(cursor), None)
    if row is None:
        return None
    value = row[0]
    return value if value is not None else None
