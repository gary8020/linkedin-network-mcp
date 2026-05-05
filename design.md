# linkedin-network-mcp Design

## Summary

`linkedin-network-mcp` is a local-first, read-only Model Context Protocol server that turns the CSV bundle returned by LinkedIn's "Get a copy of your data" export into a queryable memory layer for LLM agents. It ships as a Python CLI that ingests the export into a single SQLite file, then exposes a small set of typed tools — find people, search messages, summarize relationships, surface dormant connections — over MCP. It is the foundation under a planned dormant-tie re-engagement agent (Layer 2), and is reusable for adjacent ideas such as a privacy auditor, a post ghost-writer, or a job-search co-pilot.

The server contains no LLM dependency and makes no network calls. All intelligence lives in clients (Claude Desktop, Claude Code, Cursor, custom agents) that consume the tools.

## Goals

- One-command ingest from a raw LinkedIn export ZIP to a queryable local database.
- A small, stable tool surface usable from any MCP-aware LLM client.
- Transparent, explainable signals — tie strength, dormancy — that a non-engineer can audit.
- Zero telemetry, zero outbound network calls, single-file SQLite storage.
- A foundation that other agents and skills can build on without modification.

## Non-goals (v1)

- Live scraping of LinkedIn. The export is a snapshot; freshness is a v2 problem.
- Two-way write actions such as sending DMs or posting.
- Embeddings or vector search. SQLite FTS5 covers v1; embeddings are an opt-in v2 extra.
- Multi-user or hosted deployment.
- A built-in UI. Clients are the UI.

## Data sources

The LinkedIn export is a directory of CSVs and sub-folders. v0.1 defaults to the smallest useful ingest set for relationship search and dormant-tie discovery:

**Self identity:** `Profile.csv`, `Email Addresses.csv`, `PhoneNumbers.csv`. These describe the account owner and are stored separately from network people.

**Career context:** `Positions.csv`, `Education.csv`, `Skills.csv`, `Projects.csv`.

**Network:** `Connections.csv` (1st-degree connections with company/title/connect-date), `Invitations.csv`.

**Communications:** `messages.csv` — full DM history with profile URLs where available.

**Relationship signals:** `Endorsement_Given_Info.csv`, `Endorsement_Received_Info.csv`.

Deferred or opt-in:

- `ImportedContacts.csv` is excluded from entity resolution. These are phone-book contacts with no LinkedIn URLs and high privacy risk. A raw mirror can be enabled later with `--include-sensitive`, but it contributes to no v0.1 tool.
- `Member_Follows.csv`, `Company Follows.csv`, `Shares.csv`, `Comments.csv`, `Reactions.csv`, and `SearchQueries.csv` are deferred until the tools that need them ship. `Member_Follows.csv` contains names without profile URLs. `Reactions.csv` and `Comments.csv` do not reliably identify post authors.
- `Inferences_about_you.csv`, `Ad_Targeting.csv`, `Ads Clicked.csv`, `Logins.csv`, `Jobs/`, `Verifications/`, receipts, security challenges, and related behavioral/account files are opt-in only. They are useful for future privacy and job-search agents, but they are not needed for v0.1 relationship tools.

LinkedIn CSVs are treated as hostile input, not clean tables. The ingest layer must handle preambles before the real header (`Connections.csv` does this), multiline fields (`messages.csv` does this), duplicate column names (`Ad_Targeting.csv` does this), optional and missing columns, blank profile URLs, and multiple date formats.

## Architecture

Single package (`linkedin-network-mcp`) for v1. Layer 2 (`dormant-tie-agent`) is added as a separate package in v0.2.

```
+------------------------------+
|  Layer 2: dormant-tie-agent  |   prompt-pack / skill / subagent  [v0.2]
|  (or any other consumer)     |   v  MCP tool calls
+------------------------------+
|  Layer 1: linkedin-network-mcp       |   read-only tools + signals
|  - ingest pipeline           |
|  - MCP server (fastmcp)      |
+------------------------------+
|  SQLite (single file)        |   the only persistent state
+------------------------------+
              ^ ingest
   raw LinkedIn export ZIP
```

## Tooling

- **MCP framework**: `fastmcp` — decorator-driven tool definitions, minimal boilerplate
- **Package manager**: `uv` — dev environment, lockfile, and `uvx` end-user runtime
- **CLI framework**: `Typer` — annotation-driven subcommands, auto-generated help text
- **Tool handlers**: synchronous throughout — local single-user server, no I/O concurrency needed
- **Package layout**: `src/linkedin_mcp/` — standard src layout for PyPI distribution

```
linkedin-network-mcp/
├── src/
│   └── linkedin_mcp/
│       ├── __init__.py
│       ├── cli.py        # ingest, serve, doctor subcommands (Typer)
│       ├── server.py     # fastmcp app + tool definitions
│       ├── db.py         # SQLite connection + schema creation
│       ├── ingest/       # per-file parsers
│       └── models.py     # Pydantic return types
├── tests/
├── pyproject.toml
└── design.md
```

## Layer 1: linkedin-network-mcp

### Storage

SQLite, single file, default location `~/.linkedin-network-mcp/linkedin.db`. Rationale: portable, zero-server, FTS5 built in, no ops, easy backups, works on every OS Claude Desktop runs on.

### Ingest pipeline

```
ZIP / dir  ->  per-file parsers  ->  staging tables
                                          v
                                    entity resolution
                                          v
                                    derived columns
                                          v
                                    materialized signals
                                    (tie_strength, etc.)
```

`linkedin-network-mcp ingest <path>` drops and recreates all derived tables on every run — full replace, no merge logic. The export is a snapshot; idempotency is achieved by the replace itself. User-authored `notes` on `people` rows are a v1.1 concern.

CSV normalization rules:

- Accept either a ZIP or directory and never modify the raw export.
- Parse with Python's `csv` module using `utf-8-sig`, preserving quoted multiline fields.
- Detect the real header row per file. Known preambles, such as the `Connections.csv` "Notes:" block, are skipped.
- Disambiguate duplicate headers with stable suffixes (`Company Names__2`, etc.) in raw mirrors.
- Record a `source_rows` entry for every ingested logical row with file path, row number, row hash, and raw JSON.
- Normalize profile URLs before matching: add missing scheme, lowercase host, strip trailing slash/query/hash, and keep the public slug.
- Parse dates through a small allowlist of observed LinkedIn formats and preserve the raw value when parsing fails.
- Warn, do not crash, on unknown optional columns. Fail fast only when a required v0.1 column is missing.

### Entity resolution

People are reconciled through auditable source keys, not by mutating final rows directly. Every candidate merge records the source file, source row, key type, normalized key, and confidence.

People are reconciled across sources using two tiers:

1. **Strong key**: normalized `profile_url` / public slug (`linkedin.com/in/<slug>`) when present. Covers most message senders and connections.
2. **Medium key**: normalized full name plus current company. This is used only when it yields exactly one plausible candidate and is recorded as a lower-confidence merge.

Unresolved senders in `messages.csv` are handled as follows:
- `"LinkedIn Member"` and LinkedIn system accounts (`"Liza from LinkedIn Premium"`, etc.) — messages stored with `sender_id=NULL`, no `people` row created.
- Real people with a name but no profile URL (~59 in a typical export) — `people` row created with `source='message_only'` and `profile_url=NULL`. A future export with their URL can resolve them.

`ImportedContacts.csv` has no LinkedIn URLs and no useful cross-reference keys. It is not part of v0.1 entity resolution; fuzzy name matching is intentionally deferred because false merges are more damaging than missed matches.

The `linkedin-network-mcp doctor` command reports resolution coverage, low-confidence merges, and unresolved message senders so users can see how trustworthy network queries are on their data.

Known issue: `messages.csv`'s `RECIPIENT PROFILE URLS` column is often blank in group conversations. Group messages are stored but not associated with individual people on the recipient side until v2.

### Schema (canonical)

```sql
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
  type       TEXT NOT NULL,       -- email | phone
  value      TEXT NOT NULL,
  is_primary BOOLEAN,
  raw_row_id INTEGER REFERENCES source_rows(id)
);

CREATE TABLE people (
  id              INTEGER PRIMARY KEY,
  full_name       TEXT NOT NULL,
  public_id       TEXT,
  profile_url     TEXT UNIQUE,
  current_company TEXT,
  current_title   TEXT,
  location        TEXT,
  connected_on    DATE,
  source          TEXT NOT NULL,    -- connection | message_only
  merge_confidence REAL NOT NULL DEFAULT 1.0,
  notes           TEXT
);

CREATE TABLE person_source_keys (
  id              INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL REFERENCES people(id),
  source_row_id   INTEGER NOT NULL REFERENCES source_rows(id),
  key_type        TEXT NOT NULL,       -- profile_url | public_id | name_company | message_name
  normalized_key  TEXT NOT NULL,
  confidence      REAL NOT NULL,
  UNIQUE(key_type, normalized_key, source_row_id)
);

CREATE TABLE messages (
  id                 INTEGER PRIMARY KEY,
  source_row_id      INTEGER REFERENCES source_rows(id),
  conversation_id    TEXT NOT NULL,
  conversation_title TEXT,
  sender_id          INTEGER REFERENCES people(id),  -- NULL for unresolvable senders
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
  participant_key TEXT NOT NULL,       -- me | person:<id> | name:<normalized>
  person_id       INTEGER REFERENCES people(id),
  display_name    TEXT,
  profile_url     TEXT,
  is_me           BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(conversation_id, participant_key)
);

CREATE TABLE message_participants (
  id              INTEGER PRIMARY KEY,
  message_id      INTEGER NOT NULL REFERENCES messages(id),
  participant_key TEXT NOT NULL,       -- me | person:<id> | name:<normalized>
  person_id       INTEGER REFERENCES people(id),
  display_name    TEXT,
  profile_url     TEXT,
  role            TEXT NOT NULL,       -- sender | recipient
  is_me           BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(message_id, participant_key, role)
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content,
  content='messages',
  content_rowid='id'
);

CREATE TABLE interactions (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER REFERENCES people(id),
  type         TEXT NOT NULL,       -- msg_sent | msg_received
                                    -- | endorsement_given | endorsement_received
  occurred_at  TIMESTAMP NOT NULL,
  ref_id       INTEGER,
  metadata     JSON
);

CREATE TABLE tie_strength (
  person_id                   INTEGER PRIMARY KEY REFERENCES people(id),
  score                       REAL NOT NULL,  -- current score with recency decay
  active_score                REAL NOT NULL,  -- relationship strength excluding recency decay
  last_message_at             TIMESTAMP,
  last_touch_at               TIMESTAMP,
  days_since_last_message     INTEGER,
  days_since_last_touch       INTEGER,
  signals                     JSON   -- explainability breakdown per component
);

CREATE TABLE unanswered_threads (
  conversation_id TEXT PRIMARY KEY,
  last_message_id INTEGER REFERENCES messages(id),
  last_sent_at    TIMESTAMP NOT NULL,
  age_days        INTEGER NOT NULL,
  is_group        BOOLEAN NOT NULL
);

-- Direct mirrors (one table per source CSV):
-- v0.1 default: positions, skills, projects, education, invitations,
-- endorsements_given, endorsements_received.
-- Optional sensitive mirrors are created only with --include-sensitive.
```

`interactions` is the unified touchpoint stream — messages and endorsements only. Reactions cannot be mapped to individuals from the export format (no post-author profile URL in `Reactions.csv`) so they are excluded. Every dormancy and tie-strength query reads from `interactions`; this avoids `UNION`ing source tables on every call. Message/conversation participant tables are normalized instead of storing recipient IDs as JSON so group conversations, search filters, and indexes remain tractable.

### Tie-strength formula

A linear model in v1 — no ML. Each component is normalized to `[0, 1]`.

```
active_score = 0.60 * f_message_volume       # log-scaled bidirectional message count
             + 0.25 * f_first_degree         # 1.0 if 1st-degree connection in Connections.csv
             + 0.15 * f_endorsement_overlap  # endorsements given/received either direction

score = 0.70 * active_score
      + 0.30 * f_message_recency              # exp(-days_since_last_message / 365)
```

Engagement (comments + reactions on others' content) is excluded from v1: `Reactions.csv` has no per-person signal, and comment volume is negligible in a typical export (3 comments observed).

`last_message_at` is based only on DMs. `last_touch_at` is the most recent event in `interactions`, including endorsements. Dormancy uses `last_message_at`; endorsements should not make an otherwise silent relationship look active.

Unanswered outbound state is stored at the conversation level. A person-level projection is returned only for one-to-one conversations; group conversations are marked separately because "unanswered" is ambiguous there.

Stored alongside its `signals` JSON, so a user (or an agent) can inspect *why* a score is what it is. Weights are configurable via `~/.linkedin-network-mcp/config.toml`.

### Tool surface (v0.1 — 8 tools)

```python
# People
find_person(query: str, limit: int = 10) -> list[Person]
get_person(person_id: int) -> PersonDetail
list_connections(
    company: str | None = None,
    title_contains: str | None = None,
    sort: Literal["tie_strength", "connected_on", "last_message", "last_touch"] = "tie_strength",
    limit: int = 50,
) -> list[Person]

# Messages
search_messages(
    query: str,                       # plain text by default
    person_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    advanced_fts: bool = False,        # opt into raw FTS5 syntax
    limit: int = 20,
) -> list[MessageHit]
list_conversations(person_id: int, limit: int = 20) -> list[Conversation]
get_conversation(conversation_id: str, limit: int = 100) -> list[Message]
summarize_relationship(person_id: int) -> RelationshipSignals

# Network
dormant_connections(
    min_silent_days: int = 180,
    min_active_tie_strength: float = 0.3,
    limit: int = 30,
) -> list[DormantTie]
```

Deferred to v0.2: `find_at_company`, `list_unanswered_threads`, `list_my_posts`, `recent_searches`, `inferences_about_me`.

Tools return structured data — never LLM-generated prose. `summarize_relationship` returns counts, dates, and last-three-message-subjects; "summarize" refers to the *aggregation*, not text generation. The agent decides whether to feed the result to a model.

### Return types

```python
class Person(BaseModel):
    id: int
    full_name: str
    profile_url: str | None
    current_company: str | None
    current_title: str | None
    tie_strength: float
    active_tie_strength: float
    last_message_at: datetime | None
    last_touch_at: datetime | None
    days_since_last_message: int | None

class Conversation(BaseModel):
    conversation_id: str
    title: str | None
    participant_names: list[str]
    last_message_at: datetime | None
    message_count: int
    is_group: bool
    unanswered_outbound: bool

class RelationshipSignals(BaseModel):
    person: Person
    conversation_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    last_touch_at: datetime | None
    messages_in: int
    messages_out: int
    last_three_subjects: list[str]
    unanswered_outbound: bool

class DormantTie(BaseModel):
    person: Person
    last_topic: str | None             # heuristic from last subject / first message line
    silent_for_days: int
    active_tie_strength: float
    outbound_unanswered: bool
```

## Layer 2: dormant-tie-agent

A separate package (`dormant-tie-agent`) shipped as either a Claude Code skill, a Cowork plugin, or a CLI invoking the Anthropic SDK — all three from the same prompt and orchestration code. The loop:

1. `dormant_connections(min_silent_days=180, min_active_tie_strength=0.4)` → candidates.
2. For each candidate: `summarize_relationship(p)` plus, optionally, `list_conversations(p)` and `get_conversation(conversation_id)`.
3. The LLM ranks candidates on (a) is there a natural reason to reach out, and (b) would re-engagement be welcome based on the tone of past messages.
4. For the top N: the LLM drafts a personalized message. Style corpus from `list_my_posts()` (v0.2+); few-shot to match the user's voice.
5. Output a weekly digest: candidate, reason, draft, and a "send via LinkedIn" link.

No autosend. v1 is draft-only.

## CLI and packaging

Distributed via PyPI:

```
pip install linkedin-network-mcp
# or
uvx linkedin-network-mcp serve --db ~/.linkedin-network-mcp/linkedin.db
```

Subcommands:

- `linkedin-network-mcp ingest <path>` — ZIP or directory. Full replace on every run.
- `linkedin-network-mcp serve` — stdio MCP server. Default for client config.
- `linkedin-network-mcp doctor` — DB stats, resolution coverage, schema version.

### Claude Desktop integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uvx",
      "args": [
        "linkedin-network-mcp",
        "serve",
        "--db",
        "/Users/<you>/.linkedin-network-mcp/linkedin.db"
      ]
    }
  }
}
```

The same configuration shape works in Claude Code (`claude mcp add`), Cursor, and Continue.

## Privacy posture

The data is sensitive: DMs, IPs from `Logins.csv`, phone contacts of people who never consented to their inclusion in someone else's LinkedIn export.

- DB lives at `~/.linkedin-network-mcp/linkedin.db` by default. Path is configurable.
- Server has no outbound network calls. No telemetry. No analytics.
- README states explicitly what each tool reads and what stays local.
- `linkedin-network-mcp doctor --redact-preview` outputs a sample of returned data with PII masked, for users who want to inspect tool outputs before connecting an LLM client.
- The project does not bundle, ship, or commit any sample export data containing real PII. Test fixtures are synthetic and added before open-sourcing.
- Before the repository is initialized or published, `.gitignore` must exclude `linkedin-export/`, export ZIPs, generated SQLite databases, local MCP client configs, and OS/editor artifacts.
- v0.1 default ingest avoids high-sensitivity mirrors that are not needed by the relationship tools. `--include-sensitive` is required before importing account/security/job/privacy-advertising files.

## Known limitations

- The export is a snapshot. Titles and companies in `Connections.csv` are as of export time; people who changed jobs after export show stale info. v2 will add an optional browser-extension companion for live freshness.
- Group conversations: recipients beyond the most obvious one may be unresolved in v1, and unanswered-thread status is reported as ambiguous for groups.
- Reactions cannot be mapped to individuals — `Reactions.csv` contains only date, type, and post URL with no post-author profile URL.
- LinkedIn occasionally changes export schema. The ingest pipeline pins to known column sets and emits warnings on unrecognized columns rather than crashing.
- Medium-confidence entity merges can be wrong. The system records provenance and reports low-confidence merges rather than pretending resolution is perfect.
- The root `design.md` is the canonical design document. Copies inside raw export directories are ignored and should not be treated as source-of-truth project docs.

## Build order

1. **Privacy guardrails** — initialize repo hygiene before any commit: `.gitignore`, no real export fixtures, generated DBs ignored.
2. **CSV normalization and provenance** — parse hostile LinkedIn CSVs, populate `source_rows`, normalize URLs/dates/headers.
3. **Core ingest pipeline** — entity resolution, normalized people/messages/participants, interactions, unanswered threads, tie strength.
4. **`find_person` and `search_messages` smoke tests** — validate schema, FTS escaping, and signals against real data before building remaining tools.
5. **Remaining 6 tools** — `get_person`, `list_connections`, `list_conversations`, `get_conversation`, `summarize_relationship`, `dormant_connections`.
6. **Claude Desktop wiring** — connect and test end-to-end.

## Roadmap

**v0.1 (MVP)**: privacy guardrails, ingest pipeline, 8 core tools, Claude Desktop config recipe.

**v0.2**: `dormant-tie-agent` package, Claude Code skill packaging, schema migration system, deferred tools (`find_at_company`, `list_unanswered_threads`, `list_my_posts`), synthetic test fixtures for CI.

**v0.3**: opt-in embeddings (sqlite-vec plus a small local model) and an `inferences_about_me`-driven privacy auditor agent.

**v1.0**: stable schema, browser-extension companion for live profile freshness, additional clients verified (Cursor, Continue).

## Open questions

- Should the same DB hold multiple exports over time (history tracking) or be replaced on each ingest? v1 replaces; v2 considers append.
- Default tie-strength weights are calibrated against a single export. A simple `linkedin-network-mcp boost <person> <delta>` may be enough for user tuning without retraining.
- Storage migrations: when LinkedIn changes the export schema, require explicit `--migrate` rather than auto-migrating.
- Should low-confidence medium-key merges be applied automatically with provenance, or should they remain separate until an explicit user/agent merge command exists?

## License

MIT. The dataset is the user's own; the code that processes it is theirs to fork.
