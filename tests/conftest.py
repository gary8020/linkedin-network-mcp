from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from linkedin_mcp.ingest import ingest_export
from linkedin_mcp.server import LinkedInQueries

FIXTURE_EXPORT = Path(__file__).parent / "fixtures" / "synthetic_export"
FIXTURE_NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


@pytest.fixture
def export_copy(tmp_path: Path) -> Path:
    target = tmp_path / "export"
    shutil.copytree(FIXTURE_EXPORT, target)
    return target


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "fixture.db"
    ingest_export(FIXTURE_EXPORT, db_path, now=FIXTURE_NOW)
    return db_path


@pytest.fixture
def queries(fixture_db: Path) -> LinkedInQueries:
    return LinkedInQueries(fixture_db)
