"""Spawn the MCP server over stdio and call real tools against the seeded `repo` graph (needs FalkorDB + ingest)."""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _call(session, name, args):
    return json.loads((await session.call_tool(name, args)).content[0].text)


async def _run():
    params = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        assert {"who_knows", "impact", "why", "load_bearing", "path_between", "suggest_reviewers", "list_repos"} <= names
        lb = await _call(s, "load_bearing", {"limit": 3})
        assert lb["ok"] and len(lb["data"]["load_bearing"]) == 3
        top = lb["data"]["load_bearing"][0]["file"]
        assert (await _call(s, "who_knows", {"file": top}))["data"]["experts"]
        bad = await _call(s, "who_knows", {"file": top, "repo": "mem_alice"})  # tenant memory graphs are not repos
        assert bad["ok"] is False
        return names


def test_mcp_server_roundtrip():
    assert asyncio.run(_run())
