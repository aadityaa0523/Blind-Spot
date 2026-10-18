"""Fixed catalogue of graph tools. The model picks a tool and typed args; it never writes Cypher.

Every tool returns {"ok", "data", "path"}: `path` is the graph evidence (nodes/edges) behind the answer.
"""
import contextvars
import time

import insights
from db import repo

CURRENT = contextvars.ContextVar("repo", default="repo")  # which repo graph the tools query


def _q(cypher, params=None):
    return repo(CURRENT.get()).ro_query(cypher, params or {}).result_set


def resolve_file(name: str):
    """Resolve user text to a real File node. Returns (path|None, candidates)."""
    name = name.strip().strip("`'\"")
    rows = _q("MATCH (f:File) WHERE f.path = $n RETURN f.path", {"n": name})
    if rows:
        return rows[0][0], []
    rows = _q("MATCH (f:File) WHERE f.path ENDS WITH $n RETURN f.path ORDER BY size(f.path) LIMIT 5", {"n": "/" + name})
    if len(rows) == 1:  # exact file name, unique
        return rows[0][0], []
    rows = rows or _q("MATCH (f:File) WHERE toLower(f.path) CONTAINS toLower($n) "
                      "RETURN f.path ORDER BY size(f.path) LIMIT 5", {"n": name})
    cands = [r[0] for r in rows]
    return (cands[0], cands) if len(cands) == 1 else (None, cands)


def _unknown(name, cands):
    return {"ok": False, "data": {"error": f"'{name}' is ambiguous or not in the graph", "candidates": cands}, "path": []}


def who_knows(file: str):
    """Who has changed this file most, and how recently."""
    p, c = resolve_file(file)
    if not p:
        return _unknown(file, c)
    rows = _q("""MATCH (a:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(:File {path:$p})
                 RETURN a.name, count(c) AS commits, max(c.date) AS last ORDER BY commits DESC LIMIT 5""", {"p": p})
    data = [dict(person=r[0], commits=r[1], last_commit=r[2]) for r in rows]
    return {"ok": True, "data": {"file": p, "experts": data},
            "path": [[r[0], "AUTHORED->TOUCHED", p] for r in rows]}


def impact(file: str, limit: int = 8):
    """Files that historically change together with this one (blast radius of an edit)."""
    p, c = resolve_file(file)
    if not p:
        return _unknown(file, c)
    rows = _q("""MATCH (:File {path:$p})-[e:CO_CHANGED_WITH]->(o:File)-[:IN_MODULE]->(m:Module)
                 RETURN o.path, e.weight, m.name ORDER BY e.weight DESC LIMIT $l""", {"p": p, "l": limit})
    if not rows:
        return {"ok": True, "data": {"file": p, "co_changed": [], "note": "No co-change history (not modelled != safe)."}, "path": []}
    return {"ok": True, "data": {"file": p, "co_changed": [dict(file=r[0], times_together=r[1], module=r[2]) for r in rows]},
            "path": [[p, f"CO_CHANGED_WITH x{r[1]}", r[0]] for r in rows]}


def why(file: str, limit: int = 5):
    """Rationale: the commits/PRs touching this file that explain *why* (long commit bodies)."""
    p, c = resolve_file(file)
    if not p:
        return _unknown(file, c)
    rows = _q("""MATCH (a:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(:File {path:$p})
                 WHERE size(c.body) > 120
                 OPTIONAL MATCH (c)-[:PART_OF]->(pr:PR)
                 RETURN c.hash, c.date, c.subject, c.body, a.name, pr.number
                 ORDER BY c.date DESC LIMIT $l""", {"p": p, "l": limit})
    data = [dict(commit=r[0][:8], date=r[1], subject=r[2], rationale=r[3][:500], author=r[4], pr=r[5]) for r in rows]
    return {"ok": True, "data": {"file": p, "decisions": data},
            "path": [[r[4], f"AUTHORED commit {r[0][:8]}", p] for r in rows]}


def load_bearing(limit: int = 8):
    """PageRank over the co-change graph: the files everything else leans on."""
    rows = _q("CALL algo.pageRank('File','CO_CHANGED_WITH') YIELD node, score "
              "RETURN node.path, score ORDER BY score DESC LIMIT $l", {"l": limit})
    return {"ok": True, "data": {"load_bearing": [dict(file=r[0], score=round(r[1], 4)) for r in rows]}, "path": []}


def path_between(a: str, b: str):
    """Shortest chain of co-changing files connecting two files."""
    pa, ca = resolve_file(a)
    pb, cb = resolve_file(b)
    if not pa or not pb:
        return _unknown(a if not pa else b, ca if not pa else cb)
    rows = _q("""MATCH (x:File {path:$a}),(y:File {path:$b})
                 WITH shortestPath((x)-[:CO_CHANGED_WITH*..8]->(y)) AS p
                 RETURN [n IN nodes(p) | n.path], length(p)""", {"a": pa, "b": pb})
    rows = [r for r in rows if r[0]]
    if not rows:
        return {"ok": True, "data": {"note": "No path found (not modelled != unrelated)."}, "path": []}
    hops = rows[0][0]
    return {"ok": True, "data": {"hops": rows[0][1], "via": hops},
            "path": [[hops[i], "->", hops[i + 1]] for i in range(len(hops) - 1)]}


def suggest_reviewers(files: list[str], exclude: str = ""):
    """ACTION: rank reviewers for a change touching these files (recency-weighted authorship)."""
    resolved, bad = [], []
    for f in files:
        p, c = resolve_file(f)
        (resolved if p else bad).append(p or f)
    if not resolved:
        return _unknown(files[0] if files else "", [])
    rows = _q("""MATCH (a:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f:File) WHERE f.path IN $fs AND a.name <> $ex
                 RETURN a.name, count(c) AS commits, count(DISTINCT f) AS files, max(c.date) AS last
                 ORDER BY files DESC, commits DESC LIMIT 3""", {"fs": resolved, "ex": exclude})
    data = [dict(person=r[0], commits=r[1], files_covered=r[2], of=len(resolved), last_active=r[3]) for r in rows]
    return {"ok": True, "data": {"reviewers": data, "unresolved": bad},
            "path": [[r[0], "AUTHORED->TOUCHED", f"{r[2]}/{len(resolved)} files"] for r in rows]}


def who_knows_topic(topic: str, limit: int = 5):
    """Who has written about a topic (commit subjects and messages), and which files that work touched."""
    q = topic.strip().lower()[:60]
    if len(q) < 3:
        return {"ok": False, "data": {"error": "give a topic of at least 3 characters"}, "path": []}
    where = "WHERE toLower(c.subject) CONTAINS $t OR toLower(c.body) CONTAINS $t"
    people = _q(f"MATCH (p:Person)-[:AUTHORED]->(c:Commit) {where} RETURN p.name, count(c) AS n, max(c.date) ORDER BY n DESC LIMIT $l", {"t": q, "l": limit})
    files = _q(f"MATCH (c:Commit)-[:TOUCHED]->(f:File) {where} RETURN f.path, count(c) AS n ORDER BY n DESC LIMIT 5", {"t": q})
    if not people:
        return {"ok": True, "data": {"topic": q, "experts": [], "note": "No commit mentions this topic (not modelled != nobody knows)."}, "path": []}
    top = files[0][0] if files else ""
    return {"ok": True, "data": {"topic": q, "experts": [dict(person=r[0], commits=r[1], last_commit=r[2]) for r in people],
                                 "files": [dict(file=r[0], commits=r[1]) for r in files]},
            "path": [[r[0], f"wrote {r[1]} commits about '{q}'", top] for r in people if top]}


def bus_factor(module: str = ""):
    """Knowledge risk: modules with the fewest people behind them, and files one person wrote almost alone."""
    k = insights.knowledge(repo(CURRENT.get()))
    mods = [m for m in k["modules"] if module.lower() in m["module"].lower()][:5]
    files = [f for f in k["single_owner_files"] if module.lower() in f["path"].lower()][:5]
    return {"ok": True, "data": {"modules": mods, "single_owner_files": files},
            "path": [[m["owners"][0]["name"], f"wrote {round(m['owners'][0]['share'] * 100)}% of", m["module"]] for m in mods]}


def hidden_modules():
    """Groups of files that change as a unit (communities in the co-change graph), often across folders."""
    cl = insights.clusters(repo(CURRENT.get()))[:4]
    path = [[c["files"][0], "changes as a unit with", f] for c in cl for f in c["files"][1:4]]
    return {"ok": True, "data": {"clusters": cl}, "path": path}


TOOLS = {f.__name__: f for f in (who_knows, impact, why, load_bearing, path_between, suggest_reviewers, who_knows_topic, bus_factor, hidden_modules)}

SCHEMAS = [
    {"type": "function", "function": {"name": "who_knows", "description": "Top experts for a file by commit history.",
        "parameters": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]}}},
    {"type": "function", "function": {"name": "impact", "description": "Files that historically change together with a file (blast radius).",
        "parameters": {"type": "object", "properties": {"file": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["file"]}}},
    {"type": "function", "function": {"name": "why", "description": "Decisions and rationale from commits/PRs that touched a file.",
        "parameters": {"type": "object", "properties": {"file": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["file"]}}},
    {"type": "function", "function": {"name": "load_bearing", "description": "PageRank: the most load-bearing files in the repo.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "path_between", "description": "Shortest explainable connection between two files.",
        "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]}}},
    {"type": "function", "function": {"name": "suggest_reviewers", "description": "ACTION: pick reviewers for a change that touches these files.",
        "parameters": {"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}, "exclude": {"type": "string"}}, "required": ["files"]}}},
    {"type": "function", "function": {"name": "who_knows_topic", "description": "Who has written about a topic (e.g. 'deduplication') in commit messages, and which files that work touched.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["topic"]}}},
    {"type": "function", "function": {"name": "bus_factor", "description": "Knowledge risk: modules with the fewest people behind them and files one person wrote almost alone. Optional module name filter.",
        "parameters": {"type": "object", "properties": {"module": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "hidden_modules", "description": "Communities in the co-change graph: groups of files that change as a unit, often across folders.",
        "parameters": {"type": "object", "properties": {}}}},
]


if __name__ == "__main__":  # self-check against the seeded graph
    assert resolve_file("graph_store.py")[0].endswith("storage/graph_store.py")
    assert who_knows("api/main.py")["ok"] and impact("api/main.py")["data"]["co_changed"]
    assert load_bearing(3)["data"]["load_bearing"][0]["score"] > 0
    assert not who_knows("nope_xyz.py")["ok"]
    print("tools ok")
    print(path_between("api/main.py", "deduplicator.py"))
    print(suggest_reviewers(["api/main.py", "storage/graph_store.py"]))
