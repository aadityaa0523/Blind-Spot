"""Blindspot as an MCP server: any MCP client (Claude Desktop, Cursor, ...) can query the FalkorDB repo graphs.

Run:  python mcp_server.py        (stdio transport)
Each tool takes an optional `repo` (a graph name from `list_repos`); default is the demo repo.
"""
import json

from mcp.server.mcpserver import MCPServer

import tools
from app import repos
from tools import CURRENT

mcp = MCPServer("blindspot")


def _run(fn, repo, **kw):
    if repo not in {r["name"] for r in repos()}:
        return json.dumps({"ok": False, "error": f"unknown repo '{repo}'; call list_repos"})
    CURRENT.set(repo)
    return json.dumps(fn(**kw))


@mcp.tool()
def list_repos() -> str:
    """List the repositories available as graphs."""
    return json.dumps(repos())


@mcp.tool()
def who_knows(file: str, repo: str = "repo") -> str:
    """Top experts for a file by commit history. Returns people, commit counts and last activity, plus the graph path."""
    return _run(tools.who_knows, repo, file=file)


@mcp.tool()
def impact(file: str, limit: int = 8, repo: str = "repo") -> str:
    """Files that historically change together with this file: the blast radius of an edit."""
    return _run(tools.impact, repo, file=file, limit=limit)


@mcp.tool()
def why(file: str, limit: int = 5, repo: str = "repo") -> str:
    """Decisions and rationale: commits/PRs touching the file whose messages explain why."""
    return _run(tools.why, repo, file=file, limit=limit)


@mcp.tool()
def load_bearing(limit: int = 8, repo: str = "repo") -> str:
    """PageRank over the co-change graph: the files the rest of the codebase leans on."""
    return _run(tools.load_bearing, repo, limit=limit)


@mcp.tool()
def path_between(a: str, b: str, repo: str = "repo") -> str:
    """Shortest chain of co-changing files connecting two files."""
    return _run(tools.path_between, repo, a=a, b=b)


@mcp.tool()
def suggest_reviewers(files: list[str], exclude: str = "", repo: str = "repo") -> str:
    """Recommend reviewers for a change touching these files, ranked by authorship coverage."""
    return _run(tools.suggest_reviewers, repo, files=files, exclude=exclude)


@mcp.tool()
def who_knows_topic(topic: str, limit: int = 5, repo: str = "repo") -> str:
    """Who has written about a topic in commit messages, and which files that work touched."""
    return _run(tools.who_knows_topic, repo, topic=topic, limit=limit)


@mcp.tool()
def bus_factor(module: str = "", repo: str = "repo") -> str:
    """Knowledge risk: modules with the fewest people behind them, and files one person wrote almost alone."""
    return _run(tools.bus_factor, repo, module=module)


@mcp.tool()
def hidden_modules(repo: str = "repo") -> str:
    """Groups of files that change as a unit (communities in the co-change graph)."""
    return _run(tools.hidden_modules, repo)


if __name__ == "__main__":
    mcp.run()
