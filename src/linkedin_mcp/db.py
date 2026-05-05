from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"
DEFAULT_DB_PATH = Path.home() / ".linkedin-network-mcp" / "linkedin.db"


def connect(db_path: str | Path = DEFAULT_DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if read_only:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def reset_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS messages_ai;
        DROP TRIGGER IF EXISTS messages_ad;
        DROP TRIGGER IF EXISTS messages_au;

        DROP TABLE IF EXISTS messages_fts;
        DROP TABLE IF EXISTS unanswered_threads;
        DROP TABLE IF EXISTS tie_strength;
        DROP TABLE IF EXISTS interactions;
        DROP TABLE IF EXISTS message_participants;
        DROP TABLE IF EXISTS conversation_participants;
        DROP TABLE IF EXISTS messages;
        DROP TABLE IF EXISTS person_source_keys;
        DROP TABLE IF EXISTS people;
        DROP TABLE IF EXISTS self_contact_methods;
        DROP TABLE IF EXISTS self_profile;
        DROP TABLE IF EXISTS positions;
        DROP TABLE IF EXISTS skills;
        DROP TABLE IF EXISTS projects;
        DROP TABLE IF EXISTS education;
        DROP TABLE IF EXISTS invitations;
        DROP TABLE IF EXISTS endorsements_given;
        DROP TABLE IF EXISTS endorsements_received;
        DROP TABLE IF EXISTS source_rows;
        DROP TABLE IF EXISTS schema_info;
        """
    )
    create_schema(conn)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE schema_info (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        INSERT INTO schema_info(key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');

        CREATE TABLE source_rows (
          id                 INTEGER PRIMARY KEY,
          file_path          TEXT NOT NULL,
          logical_row_number INTEGER NOT NULL,
          row_hash           TEXT NOT NULL,
          raw_json           TEXT NOT NULL
        );

        CREATE TABLE self_profile (
          id          INTEGER PRIMARY KEY CHECK (id = 1),
          first_name  TEXT,
          last_name   TEXT,
          headline    TEXT,
          summary     TEXT,
          industry    TEXT,
          location    TEXT,
          raw_row_id   INTEGER REFERENCES source_rows(id)
        );

        CREATE TABLE self_contact_methods (
          id         INTEGER PRIMARY KEY,
          type       TEXT NOT NULL,
          value      TEXT NOT NULL,
          is_primary BOOLEAN,
          raw_row_id INTEGER REFERENCES source_rows(id)
        );

        CREATE TABLE people (
          id               INTEGER PRIMARY KEY,
          full_name        TEXT NOT NULL,
          public_id        TEXT,
          profile_url      TEXT UNIQUE,
          current_company  TEXT,
          current_title    TEXT,
          location         TEXT,
          connected_on     DATE,
          source           TEXT NOT NULL,
          merge_confidence REAL NOT NULL DEFAULT 1.0,
          notes            TEXT
        );

        CREATE TABLE person_source_keys (
          id              INTEGER PRIMARY KEY,
          person_id       INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
          source_row_id   INTEGER NOT NULL REFERENCES source_rows(id) ON DELETE CASCADE,
          key_type        TEXT NOT NULL,
          normalized_key  TEXT NOT NULL,
          confidence      REAL NOT NULL,
          UNIQUE(key_type, normalized_key, source_row_id)
        );

        CREATE TABLE messages (
          id                 INTEGER PRIMARY KEY,
          source_row_id      INTEGER REFERENCES source_rows(id) ON DELETE SET NULL,
          conversation_id    TEXT NOT NULL,
          conversation_title TEXT,
          sender_id          INTEGER REFERENCES people(id) ON DELETE SET NULL,
          sender_is_me       BOOLEAN NOT NULL,
          sent_at            TIMESTAMP NOT NULL,
          subject            TEXT,
          content            TEXT,
          folder             TEXT,
          is_draft           BOOLEAN
        );

        CREATE TABLE conversation_participants (
          id              INTEGER PRIMARY KEY,
          conversation_id TEXT NOT NULL,
          participant_key TEXT NOT NULL,
          person_id       INTEGER REFERENCES people(id) ON DELETE CASCADE,
          display_name    TEXT,
          profile_url     TEXT,
          is_me           BOOLEAN NOT NULL DEFAULT FALSE,
          UNIQUE(conversation_id, participant_key)
        );

        CREATE TABLE message_participants (
          id              INTEGER PRIMARY KEY,
          message_id      INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          participant_key TEXT NOT NULL,
          person_id       INTEGER REFERENCES people(id) ON DELETE CASCADE,
          display_name    TEXT,
          profile_url     TEXT,
          role            TEXT NOT NULL,
          is_me           BOOLEAN NOT NULL DEFAULT FALSE,
          UNIQUE(message_id, participant_key, role)
        );

        CREATE VIRTUAL TABLE messages_fts USING fts5(
          content,
          content='messages',
          content_rowid='id'
        );

        CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
          INSERT INTO messages_fts(rowid, content) VALUES (new.id, coalesce(new.content, ''));
        END;

        CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
          INSERT INTO messages_fts(messages_fts, rowid, content)
          VALUES ('delete', old.id, coalesce(old.content, ''));
        END;

        CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
          INSERT INTO messages_fts(messages_fts, rowid, content)
          VALUES ('delete', old.id, coalesce(old.content, ''));
          INSERT INTO messages_fts(rowid, content) VALUES (new.id, coalesce(new.content, ''));
        END;

        CREATE TABLE interactions (
          id          INTEGER PRIMARY KEY,
          person_id   INTEGER REFERENCES people(id) ON DELETE CASCADE,
          type        TEXT NOT NULL,
          occurred_at TIMESTAMP NOT NULL,
          ref_id      INTEGER,
          metadata    TEXT
        );

        CREATE TABLE tie_strength (
          person_id                   INTEGER PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
          score                       REAL NOT NULL,
          active_score                REAL NOT NULL,
          last_message_at             TIMESTAMP,
          last_touch_at               TIMESTAMP,
          days_since_last_message     INTEGER,
          days_since_last_touch       INTEGER,
          signals                     TEXT
        );

        CREATE TABLE unanswered_threads (
          conversation_id TEXT PRIMARY KEY,
          last_message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
          last_sent_at    TIMESTAMP NOT NULL,
          age_days        INTEGER NOT NULL,
          is_group        BOOLEAN NOT NULL
        );

        CREATE TABLE positions (
          id            INTEGER PRIMARY KEY,
          source_row_id INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          company_name  TEXT,
          title         TEXT,
          description   TEXT,
          location      TEXT,
          started_on    TEXT,
          finished_on   TEXT,
          raw_json      TEXT NOT NULL
        );

        CREATE TABLE skills (
          id            INTEGER PRIMARY KEY,
          source_row_id INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          name          TEXT,
          raw_json      TEXT NOT NULL
        );

        CREATE TABLE projects (
          id            INTEGER PRIMARY KEY,
          source_row_id INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          title         TEXT,
          description   TEXT,
          url           TEXT,
          started_on    TEXT,
          finished_on   TEXT,
          raw_json      TEXT NOT NULL
        );

        CREATE TABLE education (
          id            INTEGER PRIMARY KEY,
          source_row_id INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          school_name   TEXT,
          start_date    TEXT,
          end_date      TEXT,
          notes         TEXT,
          degree_name   TEXT,
          activities    TEXT,
          raw_json      TEXT NOT NULL
        );

        CREATE TABLE invitations (
          id                  INTEGER PRIMARY KEY,
          source_row_id       INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          from_name           TEXT,
          to_name             TEXT,
          sent_at             TIMESTAMP,
          message             TEXT,
          direction           TEXT,
          inviter_profile_url TEXT,
          invitee_profile_url TEXT,
          raw_json            TEXT NOT NULL
        );

        CREATE TABLE endorsements_given (
          id                   INTEGER PRIMARY KEY,
          source_row_id        INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          endorsement_date     TIMESTAMP,
          skill_name           TEXT,
          endorsee_first_name  TEXT,
          endorsee_last_name   TEXT,
          endorsee_profile_url TEXT,
          status               TEXT,
          person_id            INTEGER REFERENCES people(id) ON DELETE SET NULL,
          raw_json             TEXT NOT NULL
        );

        CREATE TABLE endorsements_received (
          id                   INTEGER PRIMARY KEY,
          source_row_id        INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
          endorsement_date     TIMESTAMP,
          skill_name           TEXT,
          endorser_first_name  TEXT,
          endorser_last_name   TEXT,
          endorser_profile_url TEXT,
          status               TEXT,
          person_id            INTEGER REFERENCES people(id) ON DELETE SET NULL,
          raw_json             TEXT NOT NULL
        );

        CREATE INDEX idx_people_name ON people(full_name);
        CREATE INDEX idx_people_company ON people(current_company);
        CREATE INDEX idx_people_connected ON people(connected_on);
        CREATE INDEX idx_person_source_keys_lookup ON person_source_keys(key_type, normalized_key);
        CREATE INDEX idx_messages_conversation ON messages(conversation_id, sent_at);
        CREATE INDEX idx_messages_sent_at ON messages(sent_at);
        CREATE INDEX idx_message_participants_person ON message_participants(person_id, message_id);
        CREATE INDEX idx_conversation_participants_person ON conversation_participants(person_id, conversation_id);
        CREATE INDEX idx_interactions_person ON interactions(person_id, occurred_at);
        CREATE INDEX idx_tie_strength_score ON tie_strength(score);
        """
    )
