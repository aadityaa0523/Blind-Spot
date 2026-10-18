"""Blindspot agent crew: Memory Keeper -> Investigator -> Writer -> Memory Keeper, handing off via the graph board.

`ask_stream` yields events as each agent finishes a step, so the UI shows the crew working live.
"""
import json
import os
import re
import time

from dotenv import load_dotenv

import memory
from tools import CURRENT, SCHEMAS, TOOLS

load_dotenv()
# Comma-separated fallback chain: free tiers are rate-limited per model, so a spent quota moves to the next model.
MODELS = [m.strip() for m in os.getenv("LLM_MODELS", os.getenv("LLM_MODEL", "gemini/gemini-3.7-flash")).split(",") if m.strip()]
MAX_STEPS = 2  # round 1 gathers evidence, round 2 lets the agent follow up on it (multi-hop); ~3 LLM calls per question
_dead = set()

_mcp = None
_mcp_failed = False


def mcp_tools():
    """The agent's tools come from the MCP server (AGENT_TOOLS=mcp, the default). Falls back to in-process calls if MCP cannot start."""
    global _mcp, _mcp_failed
    if os.getenv("AGENT_TOOLS", "mcp") != "mcp" or _mcp_failed:
        return None
    if _mcp is None:
        try:
            from mcp_client import MCPTools
            _mcp = MCPTools()
        except Exception as e:  # keep the app working without MCP
            _mcp_failed = True
            print("MCP tool transport unavailable, using in-process tools:", str(e)[:120])
            return None
    return _mcp
FILE_RE = re.compile(r"[\w./-]+\.(?:py|md|ts|tsx|js|jsx|json|toml|ya?ml|ipynb|rs|go|java|c|h|cpp|txt|cfg|lock|html|css)")

INVESTIGATOR = """You are the Investigator in a codebase-knowledge system. A graph database holds the git history of one \
repository (people, commits, PRs, files, co-change links). Answer the user's question ONLY by calling the provided tools; \
never guess. Call every tool you need at once, in parallel (e.g. impact + who_knows + why + suggest_reviewers for a change question). You get a second turn to follow up on results (e.g. who_knows on a file that load_bearing returned), then stop. If a tool says \
a file is ambiguous or unknown, say so instead of inventing one. When the user is CHANGING code, call gate_change; if it is MEDIUM or HIGH risk, call request_review to queue a review request (it only queues, a person approves it). Stop calling tools when you have enough evidence.
Memory from earlier sessions with this user (may be empty): {memory}
If the memory contains a \"procedure\", it is the tool plan that worked for this kind of question before: start from it and add tools only if the question needs more."""

WRITER = """You are the Writer. Using ONLY the evidence JSON below, answer the user concisely (max ~180 words). \
Cite commits/PRs/people exactly as they appear in the evidence. Name only files that appear in the evidence. \
If the evidence is empty or says 'not modelled', say that - an empty result is NOT proof of safety. \
If the user is changing code, end with a one-line 'Suggested action'. If the evidence shows a review request was queued, say it is pending human approval on the Board and give the reviewers; never say anyone was notified. \
If the memory below is relevant, mention it briefly ("last time you asked about ..."). \
Known memory about this user: {memory}
Evidence: {evidence}"""


def _llm(messages, tools=None):
    import litellm  # ~30 s and several hundred MB to import: only paid when the Ask page is actually used
    err = None
    for m in [m for m in MODELS if m not in _dead] or MODELS:
        try:
            r = litellm.completion(model=m, messages=messages, tools=tools, num_retries=1)
            r.model_used = m
            return r
        except (litellm.RateLimitError, litellm.NotFoundError, litellm.ServiceUnavailableError, litellm.InternalServerError) as e:
            err = e
            if not isinstance(e, litellm.ServiceUnavailableError):
                _dead.add(m)  # quota spent or model gone: skip it for the rest of this process
    raise err


def _names(obj):
    return {m.rsplit("/", 1)[-1] for m in FILE_RE.findall(json.dumps(obj))}


def invented_names(answer, *known_sources):
    """File names the answer mentions that appear in none of the evidence/memory/question sources."""
    known = set().union(*(_names(k) for k in known_sources))
    return {f.rsplit("/", 1)[-1] for f in FILE_RE.findall(answer)} - known


def ask_stream(user: str, question: str, repo: str = "repo"):
    CURRENT.set(repo)
    task = memory.Task(user, question)
    nodes, links, evidence = {}, [], []

    def step(agent, action, summary, **extra):
        task.step(agent, action, summary)
        return {"type": "step", "agent": agent, "action": action, "summary": summary, **extra}

    # 1. Memory Keeper reads the user's own graph first.
    t0 = time.perf_counter()
    mem = memory.recall(user, FILE_RE.findall(question))
    mem['procedure'] = memory.procedure_for(user, question)
    yield step("MemoryKeeper", "recall", f"{len(mem['facts'])} facts, {len(mem['episodes'])} past episodes" + (f", procedure: {' + '.join(mem['procedure']['tools'])} (used {mem['procedure']['uses']}x)" if mem.get("procedure") else "") + f" from graph mem_{user}",
               ms=round((time.perf_counter() - t0) * 1000), data=mem)

    # 2. Investigator picks tools (fixed catalogue) in one parallel round.
    mcp = mcp_tools()
    schemas = mcp.schemas if mcp else SCHEMAS
    via = "mcp" if mcp else None
    yield {"type": "status", "text": "Investigator is choosing graph tools" + (" (discovered over MCP)..." if mcp else "...")}
    msgs = [{"role": "system", "content": INVESTIGATOR.format(memory=json.dumps(mem))},
            {"role": "user", "content": question}]
    model_used = ""
    for _ in range(MAX_STEPS):
        resp = _llm(msgs, schemas)
        model_used = resp.model_used
        reply = resp.choices[0].message
        if not reply.tool_calls:
            break
        msgs.append(reply)
        for call in reply.tool_calls:
            name = call.function.name
            t0 = time.perf_counter()
            CURRENT.set(repo)  # streamed generators resume on different threads: set the repo right before each call
            try:
                args = json.loads(call.function.arguments or "{}")
                if mcp:
                    out = mcp.call(name, args, repo)
                else:
                    out = TOOLS[name](**args) if name in TOOLS else {"ok": False, "data": {"error": "unknown tool"}, "path": []}
            except Exception as e:  # bad args from the model must not crash the crew
                args, out = {}, {"ok": False, "data": {"error": str(e)[:200]}, "path": []}
            ms = round((time.perf_counter() - t0) * 1000)
            evidence.append({"tool": name, "args": args, **out})
            for a, rel, b in out["path"]:
                for n in (a, b):
                    nodes.setdefault(n, dict(id=n))
                links.append(dict(source=a, target=b, label=rel))
            msgs.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(out)[:6000]})
            yield step("Investigator", name, json.dumps(out["data"])[:200], args=args, ok=out["ok"], ms=ms, via=via, data=out["data"], path=out["path"])

    # 3. Writer answers from evidence only; unverifiable file names are rejected.
    yield {"type": "status", "text": "Writer is composing an answer from the evidence..."}
    answer = _llm([{"role": "system", "content": WRITER.format(memory=json.dumps(mem), evidence=json.dumps(evidence)[:12000])},
                   {"role": "user", "content": question}]).choices[0].message.content or ""
    invented = invented_names(answer, evidence, mem, question)
    if invented:
        answer = ("[Answer withheld: it named files not found in the graph: " + ", ".join(sorted(invented)) +
                  "]\nEvidence-only summary:\n" + "\n".join(f"- {e['tool']}: {json.dumps(e['data'])[:300]}" for e in evidence))
    yield step("Writer", "answer", ("rejected invented names: " + ", ".join(sorted(invented))) if invented
               else "answer verified: every file named comes from graph evidence", model=model_used, grounded=not invented)

    # 4. Memory Keeper writes back (episodic + a semantic fact) so the next session starts smarter.
    files = sorted({e["data"]["file"] for e in evidence if e["ok"] and e["data"].get("file")})
    facts = []
    if files and any(e["tool"] == "suggest_reviewers" for e in evidence):
        facts.append(f"User asked about reviewers for: {', '.join(f.rsplit('/', 1)[-1] for f in files)}")
    elif files:
        facts.append(f"User is interested in {files[0].rsplit('/', 1)[-1]}")
    memory.remember(user, question, answer, files, facts)
    learned = memory.learn_procedure(user, question, [e['tool'] for e in evidence if e['ok']])
    yield step("MemoryKeeper", "remember", f"stored 1 episode + {len(facts)} fact(s)" + (f" + procedure for '{learned}' questions" if learned else "") + f" in graph mem_{user}")

    yield {"type": "final", "answer": answer, "graph": dict(nodes=list(nodes.values()), links=links), "task": task.id,
           "grounded": not invented, "repo": repo}


def ask(user: str, question: str, repo: str = "repo"):
    """Blocking helper: run the crew and return the final event plus all steps."""
    steps, final = [], {}
    for ev in ask_stream(user, question, repo):
        if ev["type"] == "step":
            steps.append(ev)
        elif ev["type"] == "final":
            final = ev
    return {**final, "steps": steps}
