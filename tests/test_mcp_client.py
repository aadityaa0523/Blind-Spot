"""The agent's tools are discovered and called over MCP, and the model can never choose the repo."""
from mcp_client import MCPTools


def test_tools_are_discovered_over_mcp_and_repo_is_hidden_from_the_model():
    m = MCPTools()
    names = {s["function"]["name"] for s in m.schemas}
    assert {"who_knows", "impact", "request_review", "gate_change", "hidden_modules", "who_knows_topic"} <= names and "list_repos" not in names
    assert all("repo" not in s["function"]["parameters"].get("properties", {}) for s in m.schemas)
    ok = m.call("load_bearing", {"limit": 2}, "repo")
    assert ok["ok"] and len(ok["data"]["load_bearing"]) == 2
    assert m.call("load_bearing", {}, "mem_alice")["ok"] is False      # a tenant memory graph is not a repository
