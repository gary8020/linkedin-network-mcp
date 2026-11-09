# linkedin-network-mcp

Local-first, read-only MCP server for a LinkedIn "Get a copy of your data" export.

<p align="center">
  <a href="./LinkedinNetworkMcpAnimation.mp4">
    <img src="./LinkedinNetworkMcpAnimation.gif" width="700" />
  </a>
</p>

`linkedin-network-mcp` ingests a LinkedIn export directory or ZIP into one SQLite database, then exposes typed MCP tools for relationship search, message search, relationship summaries, dormant connection discovery, and keyword-ranked warm lists. It makes no network calls, does not use an LLM internally, and never sends messages or touches LinkedIn. AI clients can draft outreach from the results; a human sends it manually.

This is a fork of [0xLT/linkedin-network-mcp](https://github.com/0xLT/linkedin-network-mcp) (MIT, see `LICENSE`).

## What You Get

- A local SQLite database at `~/.linkedin-network-mcp/linkedin.db` by default.
- A stdio MCP server for Claude Code, Cursor, Claude Desktop, or another MCP client.
- Nine read-only tools:
  - `find_person`
  - `get_person`
  - `list_connections`
  - `search_messages`
  - `list_conversations`
  - `get_conversation`
  - `summarize_relationship`
  - `dormant_connections`
  - `warm_list`
- A CLI `export` command that writes ranked lists to CSV or Markdown for manual review.

## Prerequisites

- Python 3.11 or newer.
- A LinkedIn data export directory or ZIP from LinkedIn's data export settings.
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`.

## 1. Install

```bash
git clone https://github.com/gary8020/linkedin-network-mcp
cd linkedin-network-mcp
uv sync                      # installs the locked dependency set into .venv
```

Or with plain pip:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Either way the CLI is at `.venv/bin/linkedin-network-mcp`. The commands below assume the venv is active (`source .venv/bin/activate`) or that you prefix them with `uv run`.

## 2. Get Your LinkedIn Export

Request your data from LinkedIn's data/privacy export page ("Get a copy of your data"). Include messages. You can pass either the unzipped export directory or the original ZIP; file names are matched case-insensitively (`messages.csv` or `Messages.csv`), and a single nested top-level folder is fine.

Files used:

- `Profile.csv`, `Email Addresses.csv`, `PhoneNumbers.csv`
- `Positions.csv`, `Education.csv`, `Skills.csv`, `Projects.csv`
- `Connections.csv` (required)
- `Invitations.csv`
- `messages.csv`
- `Endorsement_Given_Info.csv`, `Endorsement_Received_Info.csv`

## 3. Ingest The Export

```bash
linkedin-network-mcp ingest ~/Downloads/Basic_LinkedInDataExport.zip
```

The default database is `~/.linkedin-network-mcp/linkedin.db`; pass `--db PATH` to use another (for example one database per client export). Ingest is a full replace: re-running it rebuilds the database from the export snapshot.

Expected output shape:

```json
{
  "db_path": "/Users/you/.linkedin-network-mcp/linkedin.db",
  "source_path": "/Users/you/Downloads/Basic_LinkedInDataExport.zip",
  "source_rows": 1937,
  "people": 1035,
  "messages": 908,
  "conversations": 325,
  "warnings": []
}
```

If no messages file is found, ingest still succeeds but prints `warning: No messages.csv found ...`; DM search, DM-based scores, and dormant ties will be empty. Ingest fails without touching the existing database if `Connections.csv` is missing.

After upgrading this package, re-run `ingest`: tools refuse to run against a database built with an older schema.

## 4. Check The Database

```bash
linkedin-network-mcp doctor --redact-preview
```

`doctor` reports schema version, row counts, message coverage, unresolved message senders, and a masked sample of tool output.

## 5. Warm List (7FSS sponsor prospects by default)

`warm_list` ranks **every** first-degree connection, including people you have never messaged:

```text
score = (w_keyword * keyword + w_dm * dm_history + w_endorse * endorsements + w_connected * connected_recency) / sum(weights)
```

- `keyword`: distinct keyword hits in company + title, divided by `keyword_saturation` (default 2), capped at 1.
- `dm_history`: log-scaled DM count in both directions (50+ messages = 1).
- `endorsements`: endorsements given or received (5+ = 1).
- `connected_recency`: `exp(-days_since_connected / connected_decay_days)`, measured from the ingest date.

Each result includes its components, matched keywords, message counts, and the person's last inbound message, so you can see why someone ranks where they do.

Default keywords (7 Figure Seller Summit sponsor profile): `saas`, `agency`, `agencies`, `3pl`, `amazon`, `shopify`, `ecommerce`, `e-commerce`, `partnership`, `marketing`, `sponsorship`, `founder`, `cofounder`, `ceo`, `head of`. Keywords match case-insensitively at the start of a word (`partnership` matches "Partnerships", `founder` matches "Co-Founder"); Japanese keywords match as substrings.

Run it from the shell:

```bash
linkedin-network-mcp query warm_list '{"limit":25}'
linkedin-network-mcp query warm_list '{"require_keyword":true,"limit":100}'
linkedin-network-mcp query warm_list '{"keywords":["recruiter","talent"],"require_keyword":true}'
```

### Configure keywords and weights

```bash
linkedin-network-mcp init-config      # writes ~/.linkedin-network-mcp/config.toml
```

```toml
[warm_list]
keywords = ["saas", "agency", "3pl", "amazon", "shopify", "ecommerce", "partnership", "marketing", "sponsorship", "founder", "ceo", "head of"]
keyword_saturation = 2
connected_decay_days = 1095

[warm_list.weights]
keyword = 0.40
dm_history = 0.30
endorsements = 0.15
connected_recency = 0.15
```

The config is re-read on every `warm_list` call, so edits apply without restarting your MCP client. Config lookup order: `--config PATH`, then `$LINKEDIN_NETWORK_MCP_CONFIG`, then `~/.linkedin-network-mcp/config.toml`, then built-in defaults.

## 6. Export Ranked Lists (CSV / Markdown)

```bash
linkedin-network-mcp export warm_list '{"limit":5000}' --format csv --out exports/warm_list.csv
linkedin-network-mcp export warm_list '{"require_keyword":true,"limit":200}' --format md --out exports/sponsors.md
linkedin-network-mcp export dormant_connections '{"min_silent_days":180}' --format csv --out exports/dormant.csv
linkedin-network-mcp export list_connections '{"company":"agency","limit":200}' --format csv
```

Exportable tools: `warm_list`, `dormant_connections`, `list_connections`. Without `--out`, output goes to stdout. CSV files are UTF-8 with a BOM so Excel shows Japanese names correctly, and cells that start with `=`, `+`, `-`, or `@` are prefixed with `'` so spreadsheets do not run them as formulas. Export only reads the database: it does not draft or send anything. `exports/` and all `*.csv` files are git-ignored.

## 7. Connect An MCP Client

The server uses stdio, so your MCP client launches it. Use absolute paths.

### Claude Code

```bash
cd linkedin-network-mcp
claude mcp add linkedin --scope user -- "$PWD/.venv/bin/linkedin-network-mcp" serve --db "$HOME/.linkedin-network-mcp/linkedin.db"
claude mcp list            # should show linkedin as connected
```

Add `--config /abs/path/config.toml` after `serve` to use a non-default config.

### Cursor

Add to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "/Users/<you>/linkedin-network-mcp/.venv/bin/linkedin-network-mcp",
      "args": ["serve", "--db", "/Users/<you>/.linkedin-network-mcp/linkedin.db"]
    }
  }
}
```

Then open Cursor Settings → MCP and check that `linkedin` is enabled and lists 9 tools.

### Claude Desktop

Add the same `mcpServers` block to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and restart Claude Desktop.

`mcp.json` and `claude_desktop_config.json` are git-ignored because they contain private paths.

## Example Prompts In An MCP Client

- "Run warm_list with require_keyword and show the top 25 sponsor prospects with why each ranks there."
- "List dormant connections I have not messaged in 180 days, with their last inbound message, and draft a short re-engagement note for each. I will send them myself."
- "Search my LinkedIn messages for スポンサー and summarize the conversations."
- "Find 山田太郎 and summarize our relationship."

The server returns structured data only. Any prose or draft is generated by your MCP client.

## Tool Reference

### People

```python
find_person(query: str, limit: int = 10) -> list[Person]
```

Search people by name, company, title, or LinkedIn public ID. Names match in either order and with or without spaces (`山田 太郎`, `太郎 山田`, `山田太郎`); full-width characters are normalized. The name from `Connections.csv` is kept as the canonical name.

```python
get_person(person_id: int) -> PersonDetail
```

One person with profile fields, tie strength, last interaction dates, and explainable signals.

```python
list_connections(
    company: str | None = None,
    title_contains: str | None = None,
    sort: "tie_strength" | "connected_on" | "last_message" | "last_touch" = "tie_strength",
    limit: int = 50,
) -> list[Person]
```

First-degree connections, optionally filtered by company or title.

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

Search DMs. Plain-text terms are prefix-matched and ANDed (`sponsor` finds "sponsoring" and "sponsorship"). If full-text search finds nothing, every term is matched as a substring. Queries containing Japanese (or other CJK) characters always use substring matching, because SQLite's default tokenizer does not split Japanese text into words. Set `advanced_fts=true` to pass raw FTS5 syntax.

```python
list_conversations(person_id: int, limit: int = 20) -> list[Conversation]
get_conversation(conversation_id: str, limit: int = 100) -> list[Message]
summarize_relationship(person_id: int) -> RelationshipSignals
```

Conversations involving a person, one conversation in chronological order, and aggregated relationship signals (conversation count, first/last message dates, inbound/outbound counts, last subjects, unanswered outbound state).

### Network

```python
dormant_connections(
    min_silent_days: int = 180,
    min_active_tie_strength: float = 0.3,
    limit: int = 30,
) -> list[DormantTie]
```

First-degree connections with past DM history that have gone quiet. Each result includes `last_topic` (and `last_topic_is_mine`), plus `last_inbound_at` / `last_inbound_snippet`: the last thing the other person actually said. Connections you never messaged are not dormant ties; use `warm_list` for those.

```python
warm_list(
    keywords: list[str] | None = None,   # overrides config keywords for this call
    require_keyword: bool = False,
    min_score: float = 0.0,
    limit: int = 50,                     # up to 5000
) -> list[WarmLead]
```

See section 5.

## Privacy And Data Handling

- The server is local-first and read-only; it opens the database in SQLite read-only mode.
- It makes no outbound network calls. A test fails if source code imports a network module.
- It does not send messages, post, scrape LinkedIn, or modify the export.
- The database stores full DM text unencrypted. Keep it on an encrypted disk, use one `--db` per person's export, and delete client databases when an engagement ends.
- `.gitignore` excludes databases, ZIPs, export folders, and every CSV outside `tests/fixtures/`. CI fails if such a file is tracked.
- Tests use only the synthetic export in `tests/fixtures/synthetic_export/`. Never commit real exports.

## Troubleshooting

### `No module named linkedin_mcp` / `command not found`

Run `uv sync` (or `.venv/bin/pip install -e .`) and use the absolute path `.../.venv/bin/linkedin-network-mcp`.

### `Database schema version ... does not match`

Re-run `linkedin-network-mcp ingest` on your export after upgrading.

### `Could not find CSV header`

LinkedIn may have changed the export format, or a file is malformed. Pass the top-level export directory or ZIP.

### Empty message search or dormant results

Run `doctor` and check `messages`. If it is 0, the export did not include messages; request a new export with messages selected.

### Client does not show the server

Check that the JSON is valid, the command and `--db` paths are absolute, the database exists, and the client was restarted (Claude Desktop) or the server re-enabled (Cursor Settings → MCP).

## Development

```bash
uv sync
uv run pytest -q
uv run python -m compileall -q src tests
```

CI (`.github/workflows/ci.yml`) runs the tests on Python 3.11–3.13 from `uv.lock`, checks a plain `pip install -e .` and an ingest/query/export on the synthetic fixture, and rejects tracked data files outside `tests/fixtures/`.
