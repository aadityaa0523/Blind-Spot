"""MCP client for the agent: tool discovery and tool calls go through the Model Context Protocol.

Starts mcp_server.py once (stdio), lists its tools, converts them to the model's function-calling format, and executes the
model's choices through the MCP session. The `repo` argument is hidden from the model and injected here, so the model can
never point a tool at another tenant's graph.
"""
import asyncio
import json
import os
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def to_openai(tool):
    raw = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}  # attribute name differs across mcp versions
    schema = json.loads(json.dumps(raw))
    schema.get("properties", {}).pop("repo", None)
    schema["required"] = [r for r in schema.get("required", []) if r != "repo"]
    return {"type": "function", "function": {"name": tool.name, "description": tool.description or "", "parameters": schema}}


class MCPTools:
    def __init__(self, timeout=90):
        self.loop = asyncio.new_event_loop()
        self.ready, self.err, self.session, self.schemas = threading.Event(), None, None, []
        threading.Thread(target=self._run, daemon=True).start()
        if not self.ready.wait(timeout) or self.err:
            raise RuntimeError(f"MCP server did not start: {self.err}")

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        try:
            params = StdioServerParameters(command=sys.executable, args=["mcp_server.py"], env=dict(os.environ))
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                self.session = s
                self.schemas = [to_openai(t) for t in (await s.list_tools()).tools if t.name != "list_repos"]
                self.ready.set()
                await asyncio.Event().wait()  # keep the session open for the life of the process
        except BaseException as e:  # surfaced to the constructor, unwrapping anyio task groups
            while isinstance(e, BaseExceptionGroup) and e.exceptions:
                e = e.exceptions[0]
            self.err = e
            self.ready.set()

    def call(self, name, args, repo, timeout=90):
        res = asyncio.run_coroutine_threadsafe(self.session.call_tool(name, {**args, "repo": repo}), self.loop).result(timeout)
        out = json.loads(res.content[0].text)
        out.setdefault("data", {"error": out.get("error", "tool failed")})
        out.setdefault("path", [])
        out.setdefault("ok", False)
        return out
