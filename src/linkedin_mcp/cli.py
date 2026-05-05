from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from linkedin_mcp.db import DEFAULT_DB_PATH
from linkedin_mcp.ingest import ingest_export
from linkedin_mcp.server import LinkedInQueries, run_server

app = typer.Typer(
    name="linkedin-mcp",
    no_args_is_help=True,
    help="Ingest a LinkedIn data export and serve read-only MCP tools over stdio.",
)


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(help="LinkedIn export directory or ZIP.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
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


@app.command()
def serve(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Start the stdio MCP server."""
    run_server(db)


@app.command()
def doctor(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
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
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Run an MCP tool implementation directly for debugging."""
    parsed_args = json.loads(args)
    if not isinstance(parsed_args, dict):
        raise typer.BadParameter("args must be a JSON object")
    queries = LinkedInQueries(db)
    if not hasattr(queries, tool):
        raise typer.BadParameter(f"Unknown tool: {tool}")
    fn = getattr(queries, tool)
    result = fn(**parsed_args)
    if isinstance(result, list):
        payload = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in result]
    elif hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
    else:
        payload = result
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    app()
