from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from linkedin_mcp.ingest import ingest_export
from conftest import FIXTURE_NOW


def test_capitalized_messages_csv_in_directory(export_copy: Path, tmp_path: Path) -> None:
    (export_copy / "messages.csv").rename(export_copy / "Messages.csv")
    report = ingest_export(export_copy, tmp_path / "db.sqlite", now=FIXTURE_NOW)
    assert report.messages == 7
    assert not any("No messages.csv" in warning for warning in report.warnings)


def test_capitalized_messages_csv_in_nested_zip(export_copy: Path, tmp_path: Path) -> None:
    zip_path = tmp_path / "Basic_LinkedInDataExport.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file in export_copy.iterdir():
            name = "MESSAGES.CSV" if file.name == "messages.csv" else file.name
            archive.write(file, f"Basic_LinkedInDataExport_11-09-2026/{name}")
    report = ingest_export(zip_path, tmp_path / "db.sqlite", now=FIXTURE_NOW)
    assert report.messages == 7
    assert report.people >= 8


def test_nested_directory_is_found(export_copy: Path, tmp_path: Path) -> None:
    parent = tmp_path / "downloads"
    parent.mkdir()
    export_copy.rename(parent / "Basic_LinkedInDataExport")
    report = ingest_export(parent, tmp_path / "db.sqlite", now=FIXTURE_NOW)
    assert report.messages == 7


def test_missing_messages_file_warns(export_copy: Path, tmp_path: Path) -> None:
    (export_copy / "messages.csv").unlink()
    report = ingest_export(export_copy, tmp_path / "db.sqlite", now=FIXTURE_NOW)
    assert report.messages == 0
    assert any("No messages.csv found" in warning for warning in report.warnings)


def test_missing_connections_fails_without_touching_db(export_copy: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    ingest_export(export_copy, db_path, now=FIXTURE_NOW)
    before = db_path.read_bytes()
    (export_copy / "Connections.csv").unlink()
    with pytest.raises(FileNotFoundError, match="Connections.csv"):
        ingest_export(export_copy, db_path, now=FIXTURE_NOW)
    assert db_path.read_bytes() == before
