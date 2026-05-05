from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse


@dataclass(frozen=True)
class NormalizedProfileUrl:
    url: str
    public_id: str | None
    key: str


@dataclass(frozen=True)
class CsvRecord:
    file_path: str
    logical_row_number: int
    data: dict[str, str]


class ExportSource:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._zip: zipfile.ZipFile | None = None
        self._members: dict[str, str] = {}
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self._zip = zipfile.ZipFile(self.path)
            self._members = {name.strip("/"): name for name in self._zip.namelist() if not name.endswith("/")}
        elif not self.path.is_dir():
            raise FileNotFoundError(f"LinkedIn export path does not exist or is not a ZIP: {self.path}")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "ExportSource":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def exists(self, relative_path: str) -> bool:
        if self._zip is not None:
            return self._find_member(relative_path) is not None
        return (self.path / relative_path).exists()

    def read_text(self, relative_path: str) -> str:
        if self._zip is not None:
            member = self._find_member(relative_path)
            if member is None:
                raise FileNotFoundError(relative_path)
            data = self._zip.read(member)
        else:
            data = (self.path / relative_path).read_bytes()
        return data.decode("utf-8-sig")

    def _find_member(self, relative_path: str) -> str | None:
        wanted = relative_path.strip("/")
        if wanted in self._members:
            return self._members[wanted]
        suffix = "/" + wanted
        for normalized, original in self._members.items():
            if normalized.endswith(suffix):
                return original
        return None


def read_linkedin_csv(
    source: ExportSource,
    relative_path: str,
    *,
    required_columns: tuple[str, ...] = (),
) -> list[CsvRecord]:
    if not source.exists(relative_path):
        return []

    raw_rows = list(csv.reader(io.StringIO(source.read_text(relative_path))))
    if not raw_rows:
        return []

    header_index = _find_header_row(raw_rows, required_columns)
    headers = disambiguate_headers(raw_rows[header_index])
    records: list[CsvRecord] = []
    logical_row_number = 0

    for row in raw_rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        logical_row_number += 1
        padded = row + [""] * max(0, len(headers) - len(row))
        data = {header: padded[index].strip() for index, header in enumerate(headers)}
        records.append(CsvRecord(relative_path, logical_row_number, data))

    return records


def disambiguate_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for raw in headers:
        header = raw.strip()
        counts[header] = counts.get(header, 0) + 1
        if counts[header] == 1:
            result.append(header)
        else:
            result.append(f"{header}__{counts[header]}")
    return result


def row_hash(data: dict[str, str]) -> tuple[str, str]:
    raw_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    return digest, raw_json


def parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    if text.endswith(" UTC"):
        text_no_zone = text.removesuffix(" UTC")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(text_no_zone, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass

    formats = (
        "%m/%d/%y, %I:%M %p",
        "%m/%d/%Y, %I:%M %p",
        "%d %b %Y",
        "%b %Y",
        "%B %Y",
        "%Y",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        pass
    return None


def parse_date(value: str | None) -> str | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed.date().isoformat()


def to_db_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(sep=" ", timespec="seconds")


def date_diff_days(start: str | datetime | None, now: datetime) -> int | None:
    if start is None:
        return None
    if isinstance(start, str):
        parsed = parse_datetime(start)
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(start)
            except ValueError:
                return None
    else:
        parsed = start
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    ref = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return max(0, (ref.date() - parsed.date()).days)


def normalize_profile_url(value: str | None) -> NormalizedProfileUrl | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, flags=re.IGNORECASE):
        text = "https://" + text.lstrip("/")
    parsed = urlparse(text)
    if not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    public_id: str | None = None
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "in":
        public_id = parts[1]
        path = f"/in/{public_id}"
    canonical = urlunparse(("https", host, path, "", "", ""))
    return NormalizedProfileUrl(canonical, public_id, canonical.lower())


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def full_name(*parts: str | None) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


def truthy(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return None


def parse_person_list(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    text = value.strip()
    for delimiter in (";", "\n", "|"):
        if delimiter in text:
            return [part.strip() for part in text.split(delimiter) if part.strip()]
    return [text]


def parse_profile_url_list(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    text = value.strip()
    pieces = re.split(r"[;,\n|]+", text)
    return [piece.strip() for piece in pieces if piece.strip()]


def is_system_sender(name: str | None) -> bool:
    normalized = normalize_name(name)
    if not normalized:
        return True
    if normalized in {"linkedin member", "linkedin", "linkedin premium"}:
        return True
    return " from linkedin" in normalized or normalized.startswith("linkedin ")


def _find_header_row(rows: list[list[str]], required_columns: tuple[str, ...]) -> int:
    required = {_normalize_header(column) for column in required_columns}
    fallback: int | None = None
    for index, row in enumerate(rows):
        cells = {_normalize_header(cell) for cell in row}
        if fallback is None and any(cell for cell in cells):
            fallback = index
        if required and required.issubset(cells):
            return index
    if required:
        raise ValueError(f"Could not find CSV header containing {sorted(required_columns)}")
    if fallback is None:
        raise ValueError("CSV is empty")
    return fallback


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()
