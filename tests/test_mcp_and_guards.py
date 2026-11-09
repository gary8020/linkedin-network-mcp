from __future__ import annotations

import ast
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from linkedin_mcp.server import LinkedInQueries

SRC = Path(__file__).resolve().parents[1] / "src"
EXPECTED_TOOLS = {
    "find_person",
    "get_person",
    "list_connections",
    "search_messages",
    "list_conversations",
    "get_conversation",
    "summarize_relationship",
    "dormant_connections",
    "warm_list",
}
NETWORK_MODULES = {
    "socket",
    "ssl",
    "http",
    "urllib.request",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "smtplib",
    "ftplib",
    "telnetlib",
    "xmlrpc",
    "subprocess",
}


def test_stdio_server_exposes_tools_and_answers(fixture_db: Path, tmp_path: Path) -> None:
    async def probe() -> tuple[set[str], list[str]]:
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "linkedin_mcp", "serve", "--db", str(fixture_db), "--config", str(tmp_path / "none.toml")],
            env=env,
        )
        async with Client(transport) as client:
            tools = {tool.name for tool in await client.list_tools()}
            result = await client.call_tool("warm_list", {"limit": 3})
            names = [lead["person"]["full_name"] for lead in result.structured_content["result"]]
            return tools, names

    tools, names = asyncio.run(probe())
    assert tools == EXPECTED_TOOLS
    assert names == ["Alice Agency", "Fiona Shopify", "Bob Saas"]


def test_source_imports_no_network_modules() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if any(module == banned or module.startswith(banned + ".") for banned in NETWORK_MODULES):
                    offenders.append(f"{path.relative_to(SRC)}: {module}")
    assert offenders == []


def test_server_opens_db_read_only(fixture_db: Path) -> None:
    conn = LinkedInQueries(fixture_db).connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM people")
    finally:
        conn.close()
