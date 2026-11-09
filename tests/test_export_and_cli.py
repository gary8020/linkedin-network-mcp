from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from linkedin_mcp.cli import app
from linkedin_mcp.export import csv_safe, render
from linkedin_mcp.models import Person, WarmLead
from linkedin_mcp.server import LinkedInQueries
from conftest import FIXTURE_EXPORT

runner = CliRunner()


def test_csv_neutralizes_spreadsheet_formulas() -> None:
    assert csv_safe("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    assert csv_safe("+81 Corp") == "'+81 Corp"
    assert csv_safe("@handle") == "'@handle"
    assert csv_safe("Normal Co") == "Normal Co"
    assert csv_safe(-0.5) == "-0.5"
    assert csv_safe(None) == ""


def test_markdown_escapes_pipes_and_newlines() -> None:
    lead = WarmLead(rank=1, person=Person(id=1, full_name="A | B\nC"), score=0.5)
    text = render("warm_list", [lead], "md", title="t")
    assert "A \\| B C" in text
    assert text.startswith("# t\n")


def test_empty_export_still_has_header() -> None:
    assert render("warm_list", [], "csv").startswith("rank,name,company,title,profile_url,score,")
    assert "_No results._" in render("dormant_connections", [], "md")


def test_render_warm_list_csv(queries: LinkedInQueries) -> None:
    text = render("warm_list", queries.warm_list(limit=100), "csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 8
    assert rows[0]["name"] == "Alice Agency"
    assert rows[0]["matched_keywords"] == "agency; amazon; founder"
    assert rows[0]["last_inbound_snippet"] == "Interested in sponsoring the summit booth?"
    assert {row["name"] for row in rows} >= {"太郎 山田", "Never Messaged"}


def test_cli_end_to_end(tmp_path: Path) -> None:
    db = tmp_path / "cli.db"
    result = runner.invoke(app, ["ingest", str(FIXTURE_EXPORT), "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["messages"] == 7

    config = tmp_path / "config.toml"
    result = runner.invoke(app, ["init-config", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert config.exists()

    result = runner.invoke(
        app, ["query", "warm_list", '{"limit":3}', "--db", str(db), "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert [lead["person"]["full_name"] for lead in json.loads(result.stdout)] == [
        "Alice Agency",
        "Fiona Shopify",
        "Bob Saas",
    ]

    csv_path = tmp_path / "exports" / "warm.csv"
    result = runner.invoke(
        app,
        ["export", "warm_list", '{"limit":5000}', "--format", "csv", "--out", str(csv_path), "--db", str(db)],
    )
    assert result.exit_code == 0, result.output
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert len(rows) == 8

    md_path = tmp_path / "dormant.md"
    result = runner.invoke(
        app, ["export", "dormant_connections", "--format", "md", "--out", str(md_path), "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    assert "| 1 | Alice Agency |" in md_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["export", "list_connections", '{"limit":2}', "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith("rank,name,company")


def test_cli_rejects_unknown_tools(fixture_db: Path) -> None:
    result = runner.invoke(app, ["export", "search_messages", "--db", str(fixture_db)])
    assert result.exit_code != 0
    result = runner.invoke(app, ["query", "connection", "--db", str(fixture_db)])
    assert result.exit_code != 0


def test_ingest_prints_warnings(tmp_path: Path, export_copy: Path) -> None:
    (export_copy / "messages.csv").unlink()
    result = runner.invoke(app, ["ingest", str(export_copy), "--db", str(tmp_path / "w.db")])
    assert result.exit_code == 0
    assert "warning: No messages.csv found" in result.stderr


def test_stale_schema_db_asks_for_reingest(fixture_db: Path) -> None:
    conn = sqlite3.connect(fixture_db)
    conn.execute("UPDATE schema_info SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    result = runner.invoke(app, ["query", "warm_list", "--db", str(fixture_db)])
    assert result.exit_code != 0
    assert "re-run `linkedin-network-mcp ingest`" in str(result.exception)
