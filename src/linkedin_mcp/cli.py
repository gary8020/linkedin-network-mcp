from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from linkedin_mcp.config import CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH, write_default_config
from linkedin_mcp.db import DEFAULT_DB_PATH
from linkedin_mcp.export import EXPORTABLE_TOOLS, render
from linkedin_mcp.ingest import ingest_export
from linkedin_mcp.server import LinkedInQueries, run_server

app = typer.Typer(
    name="linkedin-network-mcp",
    no_args_is_help=True,
    help="Ingest a LinkedIn data export and serve read-only MCP tools over stdio.",
)

DbOption = Annotated[Path, typer.Option("--db", help="SQLite database path.")]
ConfigOption = Annotated[
    Optional[Path],
    typer.Option(
        "--config",
        help=f"Config TOML path. Defaults to ${CONFIG_ENV_VAR} or {DEFAULT_CONFIG_PATH}; built-in defaults if missing.",
    ),
]


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(help="LinkedIn export directory or ZIP.")],
    db: DbOption = DEFAULT_DB_PATH,
    include_sensitive: Annotated[
        bool,
        typer.Option(
            "--include-sensitive",
            help="Reserved for v0.2. v0.1 does not ingest sensitive optional mirrors.",
        ),
    ] = False,
) -> None:
    """Replace the local SQLite DB with a fresh ingest of the export."""
    report = ingest_export(path, db, include_sensitive=include_sensitive)
    typer.echo(report.model_dump_json(indent=2))
    for warning in report.warnings:
        typer.echo(f"warning: {warning}", err=True)


@app.command()
def serve(db: DbOption = DEFAULT_DB_PATH, config: ConfigOption = None) -> None:
    """Start the stdio MCP server."""
    run_server(db, config)


@app.command()
def doctor(
    db: DbOption = DEFAULT_DB_PATH,
    redact_preview: Annotated[
        bool,
        typer.Option("--redact-preview", help="Include a masked sample of tool output."),
    ] = False,
) -> None:
    """Print DB stats, resolution coverage, and schema version."""
    report = LinkedInQueries(db).doctor(redact_preview=redact_preview)
    typer.echo(report.model_dump_json(indent=2))


@app.command()
def query(
    tool: Annotated[str, typer.Argument(help="Tool name to run from the shell.")],
    args: Annotated[
        str,
        typer.Argument(help="JSON object of tool arguments, for example '{\"query\":\"alice\"}'."),
    ] = "{}",
    db: DbOption = DEFAULT_DB_PATH,
    config: ConfigOption = None,
) -> None:
    """Run an MCP tool implementation directly for debugging."""
    result = run_tool(LinkedInQueries(db, config), tool, args)
    if isinstance(result, list):
        payload = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result]
    elif hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
    else:
        payload = result
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command()
def export(
    tool: Annotated[str, typer.Argument(help=f"One of: {', '.join(EXPORTABLE_TOOLS)}.")],
    args: Annotated[
        str,
        typer.Argument(help="JSON object of tool arguments, for example '{\"limit\":2000}'."),
    ] = "{}",
    fmt: Annotated[str, typer.Option("--format", "-f", help="csv or md.")] = "csv",
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output file. Prints to stdout when omitted."),
    ] = None,
    db: DbOption = DEFAULT_DB_PATH,
    config: ConfigOption = None,
) -> None:
    """Export a ranked list to CSV or Markdown for manual review. Never drafts or sends anything."""
    if tool not in EXPORTABLE_TOOLS:
        raise typer.BadParameter(f"Cannot export {tool}; expected one of: {', '.join(EXPORTABLE_TOOLS)}")
    fmt = fmt.lower()
    if fmt not in {"csv", "md", "markdown"}:
        raise typer.BadParameter("--format must be csv or md")
    items = run_tool(LinkedInQueries(db, config), tool, args)
    text = render(tool, items, fmt, title=f"{tool} export")
    if out is None:
        typer.echo(text, nl=False)
        return
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    # BOM so Excel opens Japanese names in CSV correctly.
    out.write_text(text, encoding="utf-8-sig" if fmt == "csv" else "utf-8")
    typer.echo(f"Wrote {len(items)} rows to {out}", err=True)


@app.command("init-config")
def init_config(
    config: ConfigOption = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config file.")] = False,
) -> None:
    """Write the default config (7FSS sponsor keywords and warm_list weights) for editing."""
    try:
        path = write_default_config(config, force=force)
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(str(path))


TOOL_NAMES = (
    "find_person",
    "get_person",
    "list_connections",
    "search_messages",
    "list_conversations",
    "get_conversation",
    "summarize_relationship",
    "dormant_connections",
    "warm_list",
)


def run_tool(queries: LinkedInQueries, tool: str, args: str) -> object:
    parsed_args = json.loads(args)
    if not isinstance(parsed_args, dict):
        raise typer.BadParameter("args must be a JSON object")
    if tool not in TOOL_NAMES:
        raise typer.BadParameter(f"Unknown tool: {tool}. Expected one of: {', '.join(TOOL_NAMES)}")
    return getattr(queries, tool)(**parsed_args)


def main() -> None:
    app()
