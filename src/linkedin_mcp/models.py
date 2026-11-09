from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Person(BaseModel):
    id: int
    full_name: str
    profile_url: str | None = None
    current_company: str | None = None
    current_title: str | None = None
    tie_strength: float = 0.0
    active_tie_strength: float = 0.0
    last_message_at: datetime | None = None
    last_touch_at: datetime | None = None
    days_since_last_message: int | None = None


class PersonDetail(Person):
    public_id: str | None = None
    location: str | None = None
    connected_on: str | None = None
    source: str
    merge_confidence: float
    signals: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    id: int
    conversation_id: str
    conversation_title: str | None = None
    sender_id: int | None = None
    sender_name: str | None = None
    sender_is_me: bool
    sent_at: datetime
    subject: str | None = None
    content: str | None = None
    folder: str | None = None
    is_draft: bool | None = None
    recipient_names: list[str] = Field(default_factory=list)


class MessageHit(BaseModel):
    id: int
    conversation_id: str
    conversation_title: str | None = None
    sender_id: int | None = None
    sender_name: str | None = None
    sender_is_me: bool
    sent_at: datetime
    subject: str | None = None
    snippet: str
    folder: str | None = None
    rank: float | None = None


class Conversation(BaseModel):
    conversation_id: str
    title: str | None = None
    participant_names: list[str] = Field(default_factory=list)
    last_message_at: datetime | None = None
    message_count: int
    is_group: bool
    unanswered_outbound: bool


class RelationshipSignals(BaseModel):
    person: Person
    conversation_count: int
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None
    last_touch_at: datetime | None = None
    messages_in: int
    messages_out: int
    last_three_subjects: list[str] = Field(default_factory=list)
    unanswered_outbound: bool


class DormantTie(BaseModel):
    person: Person
    last_topic: str | None = None
    last_topic_is_mine: bool = False
    last_inbound_at: datetime | None = None
    last_inbound_snippet: str | None = None
    silent_for_days: int
    active_tie_strength: float
    outbound_unanswered: bool


class IngestReport(BaseModel):
    db_path: str
    source_path: str
    source_rows: int
    people: int
    messages: int
    conversations: int
    warnings: list[str] = Field(default_factory=list)


class DoctorReport(BaseModel):
    db_path: str
    schema_version: str | None = None
    source_rows: int
    people: int
    connections: int
    message_only_people: int
    messages: int
    conversations: int
    unresolved_message_senders: int
    low_confidence_merges: int
    preview: dict[str, Any] | None = None
