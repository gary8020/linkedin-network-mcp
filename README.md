# linkedin-network-mcp

Local-first, read-only MCP server for a LinkedIn "Get a copy of your data" export.

<p align="center">
  <a href="./LinkedinNetworkMcpAnimation.mp4">
    <img src="./LinkedinNetworkMcpAnimation.gif" width="700" />
  </a>
</p>

`linkedin-network-mcp` ingests a LinkedIn export directory or ZIP into one SQLite database, then exposes typed MCP tools for relationship search, message search, relationship summaries, and dormant connection discovery. It makes no network calls and does not use an LLM internally.

## What You Get

- A local SQLite database at `~/.linkedin-network-mcp/linkedin.db` by default.
- A stdio MCP server for Claude Desktop, Claude Code, Cursor, Continue, or another MCP client.
- Eight read-only tools:
  - `find_person`
  - `get_person`
  - `list_connections`
  - `search_messages`
  - `list_conversations`
  - `get_conversation`
  - `summarize_relationship`
  - `dormant_connections`

## Prerequisites

- Python 3.11 or newer.
- A LinkedIn data export directory or ZIP from LinkedIn's data export settings.
- For MCP client usage, an MCP-aware client such as Claude Desktop or Claude Code.

The project currently runs directly from source. PyPI/`uvx` usage is the intended distribution path, but local development is the supported path in this repo today.

## 1. Get Your LinkedIn Export

Download your LinkedIn data from LinkedIn's data/privacy export page. LinkedIn may move or rename the page, but the export is usually called something like "Get a copy of your data" or "Download your data."

For the v0.1 relationship tools, the useful files are:

- `Profile.csv`
- `Email Addresses.csv`
- `PhoneNumbers.csv`
- `Positions.csv`
- `Education.csv`
- `Skills.csv`
- `Projects.csv`
- `Connections.csv`
- `Invitations.csv`
- `messages.csv`
- `Endorsement_Given_Info.csv`
- `Endorsement_Received_Info.csv`

You can pass either the unzipped export directory or the original ZIP to `linkedin-network-mcp ingest`.

## 2. Install For Local Development

From the repo root:

```bash
pip install -e .
```

If you do not want to install the package, run commands with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m linkedin_mcp --help
```

## 3. Ingest The Export

Installed package:

```bash
linkedin-network-mcp ingest /path/to/linkedin-export --db ~/.linkedin-network-mcp/linkedin.db
```

Without installing:

```bash
PYTHONPATH=src python3 -m linkedin_mcp ingest /path/to/linkedin-export --db ~/.linkedin-network-mcp/linkedin.db
```

The ingest is a full replace. Re-running it drops and rebuilds the generated tables from the export snapshot.

Expected output shape:

```json
{
  "db_path": "/Users/you/.linkedin-network-mcp/linkedin.db",
  "source_path": "/path/to/linkedin-export",
  "source_rows": 1937,
  "people": 1035,
  "messages": 908,
  "conversations": 325,
  "warnings": []
}
```

Warnings usually mean LinkedIn changed a CSV shape or a row had an unparseable date. The ingest warns on optional issues and fails only for required v0.1 columns.

## 4. Check The Database

```bash
linkedin-network-mcp doctor --db ~/.linkedin-network-mcp/linkedin.db --redact-preview
```

Without installing:

```bash
PYTHONPATH=src python3 -m linkedin_mcp doctor --db ~/.linkedin-network-mcp/linkedin.db --redact-preview
```

`doctor` reports schema version, row counts, message coverage, unresolved message senders, and a masked sample of tool output when `--redact-preview` is set.

## 5. Try A Tool From The Shell

The `query` command calls the same Python implementation used by the MCP tools.

```bash
linkedin-network-mcp query find_person '{"query":"alex","limit":5}' --db ~/.linkedin-network-mcp/linkedin.db
linkedin-network-mcp query search_messages '{"query":"roadmap","limit":5}' --db ~/.linkedin-network-mcp/linkedin.db
linkedin-network-mcp query dormant_connections '{"min_silent_days":180,"min_active_tie_strength":0.3,"limit":10}' --db ~/.linkedin-network-mcp/linkedin.db
```

Without installing:

```bash
PYTHONPATH=src python3 -m linkedin_mcp query find_person '{"query":"alex","limit":5}' --db ~/.linkedin-network-mcp/linkedin.db
```

## 6. Run The MCP Server

```bash
linkedin-network-mcp serve --db ~/.linkedin-network-mcp/linkedin.db
```

The server uses stdio, so it is normally launched by your MCP client rather than run manually in a terminal.

## Claude Desktop Setup

Add this to your Claude Desktop MCP config.

macOS path:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Local source checkout:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "python3",
      "args": [
        "-m",
        "linkedin_mcp",
        "serve",
        "--db",
        "/Users/<you>/.linkedin-network-mcp/linkedin.db"
      ],
      "env": {
        "PYTHONPATH": "/absolute/path/to/linkedin-network-mcp/src"
      }
    }
  }
}
```

Installed package:

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "linkedin-network-mcp",
      "args": [
        "serve",
        "--db",
        "/Users/<you>/.linkedin-network-mcp/linkedin.db"
      ]
    }
  }
}
```

Restart Claude Desktop after editing the config.

## Claude Code Setup

From this repo, after ingesting the database:

```bash
claude mcp add linkedin -- python3 -m linkedin_mcp serve --db ~/.linkedin-network-mcp/linkedin.db
```

If using the source checkout without installing, make sure Claude Code launches the server with `PYTHONPATH` pointing at this repo's `src` directory.

## Tool Reference

### People

```python
find_person(query: str, limit: int = 10) -> list[Person]
```

Search people by name, company, title, or LinkedIn public ID.

```python
get_person(person_id: int) -> PersonDetail
```

Return one person with profile fields, tie strength, last interaction dates, and explainable signals.

```python
list_connections(
    company: str | None = None,
    title_contains: str | None = None,
    sort: "tie_strength" | "connected_on" | "last_message" | "last_touch" = "tie_strength",
    limit: int = 50,
) -> list[Person]
```

List first-degree connections, optionally filtered by company or title.

### Messages

```python
search_messages(
    query: str,
    person_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    advanced_fts: bool = False,
    limit: int = 20,
) -> list[MessageHit]
```

Search DMs. By default, the query is treated as plain text and escaped for SQLite FTS5. Set `advanced_fts=true` only when you want to pass raw FTS5 syntax.

```python
list_conversations(person_id: int, limit: int = 20) -> list[Conversation]
```

List conversations involving a person.

```python
get_conversation(conversation_id: str, limit: int = 100) -> list[Message]
```

Return messages in one conversation in chronological order.

```python
summarize_relationship(person_id: int) -> RelationshipSignals
```

Return aggregated relationship signals: conversation count, first/last message dates, inbound/outbound counts, last subjects, and unanswered outbound state.

### Network

```python
dormant_connections(
    min_silent_days: int = 180,
    min_active_tie_strength: float = 0.3,
    limit: int = 30,
) -> list[DormantTie]
```

List first-degree connections with meaningful historical signal and stale message activity.

## Example Prompts In An MCP Client

After connecting the server, try:

- "Find people in my LinkedIn network who worked at Acme."
- "Search my LinkedIn messages for roadmap and summarize the relevant conversations."
- "List dormant connections I have not messaged in 180 days but had a strong prior relationship with."
- "Show the last conversation with person id 42."

The server returns structured data only. Any prose summary is generated by your MCP client.

## Privacy And Data Handling

- The server is local-first and read-only.
- It makes no outbound network calls.
- It does not send messages, post, scrape LinkedIn, or modify the export.
- Generated databases are ignored by `.gitignore`.
- Real LinkedIn export directories and ZIPs are ignored by `.gitignore`.
- v0.1 does not ingest high-sensitivity optional mirrors such as ad targeting, account logins, jobs, receipts, or imported phone-book contacts.

Do not commit real exports or generated SQLite databases.

## Troubleshooting

### `No module named linkedin_mcp`

Install the package:

```bash
pip install -e .
```

Or run with:

```bash
PYTHONPATH=src python3 -m linkedin_mcp --help
```

### `Could not find CSV header`

LinkedIn may have changed the export format, or the export is missing a required file. Run ingest against the top-level export directory or ZIP, not an inner folder unless that inner folder contains the CSVs.

### `unable to open database file`

Check that the parent directory exists and is writable. The default path `~/.linkedin-network-mcp/linkedin.db` is created automatically by the ingest command.

### Empty Search Results

Run `doctor` first to confirm that messages were ingested:

```bash
linkedin-network-mcp doctor --db ~/.linkedin-network-mcp/linkedin.db
```

Then try a broader query or omit `person_id`.

### Claude Desktop Does Not Show The Server

Check that:

- The JSON config is valid.
- The `PYTHONPATH` value is an absolute path when running from source.
- The `--db` path points to a database created by `ingest`.
- Claude Desktop was restarted after editing the config.

## Development

Run tests:

```bash
PYTHONPATH=src python3 -m pytest -q
```

Run syntax checks:

```bash
PYTHONPATH=src python3 -m compileall -q src tests
```
