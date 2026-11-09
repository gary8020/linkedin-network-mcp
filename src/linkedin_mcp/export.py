from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from linkedin_mcp.models import DormantTie, Person, WarmLead

EXPORTABLE_TOOLS = ("warm_list", "dormant_connections", "list_connections")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def person_columns(person: Person) -> dict[str, Any]:
    return {
        "name": person.full_name,
        "company": person.current_company,
        "title": person.current_title,
        "profile_url": person.profile_url,
    }


def warm_lead_row(lead: WarmLead) -> dict[str, Any]:
    return {
        "rank": lead.rank,
        **person_columns(lead.person),
        "score": lead.score,
        "matched_keywords": "; ".join(lead.matched_keywords),
        "keyword_score": lead.components.get("keyword"),
        "dm_history_score": lead.components.get("dm_history"),
        "endorsement_score": lead.components.get("endorsements"),
        "connected_recency_score": lead.components.get("connected_recency"),
        "connected_on": lead.connected_on,
        "messages_sent": lead.messages_sent,
        "messages_received": lead.messages_received,
        "endorsements": lead.endorsements,
        "last_message_at": lead.person.last_message_at,
        "last_inbound_at": lead.last_inbound_at,
        "last_inbound_snippet": lead.last_inbound_snippet,
    }


def dormant_tie_row(tie: DormantTie, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        **person_columns(tie.person),
        "silent_for_days": tie.silent_for_days,
        "active_tie_strength": round(tie.active_tie_strength, 4),
        "outbound_unanswered": tie.outbound_unanswered,
        "last_message_at": tie.person.last_message_at,
        "last_topic": tie.last_topic,
        "last_topic_is_mine": tie.last_topic_is_mine,
        "last_inbound_at": tie.last_inbound_at,
        "last_inbound_snippet": tie.last_inbound_snippet,
    }


def connection_row(person: Person, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        **person_columns(person),
        "tie_strength": round(person.tie_strength, 4),
        "active_tie_strength": round(person.active_tie_strength, 4),
        "last_message_at": person.last_message_at,
        "days_since_last_message": person.days_since_last_message,
    }


ROW_BUILDERS: dict[str, Callable[[Sequence[Any]], list[dict[str, Any]]]] = {
    "warm_list": lambda items: [warm_lead_row(item) for item in items],
    "dormant_connections": lambda items: [dormant_tie_row(item, rank) for rank, item in enumerate(items, 1)],
    "list_connections": lambda items: [connection_row(item, rank) for rank, item in enumerate(items, 1)],
}


def rows_for(tool: str, items: Sequence[Any]) -> list[dict[str, Any]]:
    if tool not in ROW_BUILDERS:
        raise ValueError(f"Cannot export {tool!r}; expected one of {', '.join(EXPORTABLE_TOOLS)}")
    return ROW_BUILDERS[tool](items)


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def csv_safe(value: Any) -> str:
    """Neutralize spreadsheet formulas: names and titles come from other people's profiles."""
    text = format_value(value)
    if isinstance(value, str) and text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([csv_safe(row.get(column)) for column in columns])
    return buffer.getvalue()


def markdown_cell(value: Any) -> str:
    text = format_value(value)
    return " ".join(text.split()).replace("\\", "\\\\").replace("|", "\\|")


def to_markdown(rows: list[dict[str, Any]], columns: list[str], title: str) -> str:
    lines = [f"# {title}", ""]
    if not rows:
        lines.append("_No results._")
        return "\n".join(lines) + "\n"
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join(" --- " for _ in columns) + "|")
    for row in rows:
        lines.append("| " + " | ".join(markdown_cell(row.get(column)) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def render(tool: str, items: Sequence[Any], fmt: str, title: str | None = None) -> str:
    rows = rows_for(tool, items)
    columns = list(rows[0].keys()) if rows else default_columns(tool)
    if fmt == "csv":
        return to_csv(rows, columns)
    if fmt in {"md", "markdown"}:
        return to_markdown(rows, columns, title or tool)
    raise ValueError(f"Unknown export format {fmt!r}; expected csv or md")


def default_columns(tool: str) -> list[str]:
    empty_person = Person(id=0, full_name="")
    samples: dict[str, dict[str, Any]] = {
        "warm_list": warm_lead_row(WarmLead(rank=0, person=empty_person, score=0)),
        "dormant_connections": dormant_tie_row(
            DormantTie(person=empty_person, silent_for_days=0, active_tie_strength=0, outbound_unanswered=False), 0
        ),
        "list_connections": connection_row(empty_person, 0),
    }
    return list(samples[tool].keys())
