"""Merge gate: given the files a change touches, say how risky it is, from the graph alone (no LLM).

Signals (each shown to the user with its points, so the score is explainable):
  * criticality  - PageRank percentile of the most load-bearing touched file (algo.pageRank over CO_CHANGED_WITH)
  * companions   - files that changed with these files in >= MIN_CONF of their commits but are NOT in this change
  * breadth      - number of modules touched
  * size         - number of files
"""
import bisect
import json
import re
import urllib.request

import tools
from db import repo
from tools import CURRENT

MIN_CONF, MIN_WEIGHT = 0.25, 3
META_SUFFIXES = (".md", ".mdx", ".txt", ".lock", ".rst")  # docs/lockfiles co-change a lot but are not "critical code"


def is_meta(path):
    return path.lower().endswith(META_SUFFIXES) or path.rsplit("/", 1)[-1].upper().startswith(("LICENSE", "CHANGELOG"))
PR_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)/?$")


def _cache_path():
    import os
    return os.getenv("PR_CACHE", "data/pr_cache.json")


def _cache_load():
    """Bundled real PRs (seed/pr_cache.json) overlaid by whatever was fetched at runtime."""
    out = {}
    for p in ("seed/pr_cache.json", _cache_path()):
        try:
            out.update(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass
    return out


def fetch_pr(url: str):
    """Changed files + title for a public GitHub PR (set GITHUB_TOKEN to raise the 60 req/h anonymous limit).
    Closed/merged PRs never change, so they are cached on disk and cost no API calls on repeat."""
    import os
    m = PR_RE.match(url.strip())
    if not m:
        raise ValueError("Use a URL like https://github.com/owner/name/pull/123")
    o, r, n = m.groups()
    ckey = f"{o}/{r}#{n}".lower()
    cached = _cache_load().get(ckey)
    if cached:
        return cached
    headers = {"User-Agent": "blindspot", "Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    get = lambda p: json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{o}/{r}/{p}", headers=headers), timeout=20))
    meta = get(f"pulls/{n}")
    files = get(f"pulls/{n}/files?per_page=100")
    pr = dict(owner=o, name=r, number=int(n), title=meta["title"], author=meta["user"]["login"], state=meta["state"],
                files=sorted({n for f in files for n in (f["filename"], f.get("previous_filename")) if n}),  # renames/deletes count as touched
                truncated=meta.get("changed_files", 0) > len(files))
    if pr["state"] != "open":  # open PRs can still change
        try:
            mine = json.load(open(_cache_path(), encoding="utf-8"))
        except Exception:
            mine = {}
        mine[ckey] = pr
        os.makedirs(os.path.dirname(_cache_path()) or ".", exist_ok=True)
        json.dump(mine, open(_cache_path(), "w", encoding="utf-8"))
    return pr


def _q(cypher, params=None):
    return repo(CURRENT.get()).ro_query(cypher, params or {}).result_set


_BASE: dict[str, list[int]] = {}
HIGH_PCT, MED_PCT = 80, 50  # HIGH = riskier than 80% of this repo's past changes, MEDIUM = riskier than 50%


def baseline(name: str) -> list[int]:
    """Sorted gate scores of up to 200 recent commits in this repo. Verdicts are relative to this distribution."""
    if name in _BASE:
        return _BASE[name]
    CURRENT.set(name)
    rows = _q("MATCH (c:Commit)-[:TOUCHED]->(f:File) RETURN c.seq, collect(f.path) ORDER BY c.seq DESC LIMIT 400")
    scores = []
    for _, fs in rows:
        if 2 <= len(fs) <= 40 and len(scores) < 200:
            r = analyze(fs, light=True)
            if r["score"] is not None:
                scores.append(r["score"])
    _BASE[name] = sorted(scores)
    return _BASE[name]


def analyze(files: list[str], author: str = "", light: bool = False, base: list[int] | None = None):
    resolved, unknown = [], []
    for f in files:
        (resolved if _q("MATCH (x:File {path:$p}) RETURN 1", {"p": f}) else unknown).append(f)
    out = dict(files=files, known=resolved, new_or_unknown=unknown, factors=[], companions=[], reviewers=[], decisions=[], path=[])
    if not resolved:
        out.update(score=None, verdict="UNKNOWN", summary="None of these files exist in the graph, so there is no history to judge them by. Not modelled is not safe.")
        return out

    # criticality: PageRank percentile of the most load-bearing touched file
    ranked = [r[0] for r in _q("CALL algo.pageRank('File','CO_CHANGED_WITH') YIELD node, score RETURN node.path ORDER BY score DESC") if not is_meta(r[0])]
    top_rank = min((ranked.index(f) for f in resolved if f in ranked), default=None)
    crit = 0.0 if top_rank is None else 1 - top_rank / max(len(ranked), 1)
    crit_pts = round(35 * crit ** 2)  # squared: only genuinely central files score high
    if top_rank is not None:
        out["factors"].append(dict(name="Load-bearing file", points=crit_pts, max=35,
                                   detail=f"{ranked[top_rank]} ranks #{top_rank + 1} of {len(ranked)} files by PageRank"))

    # missing companions: confidence = P(other file changes | this file changes)
    rows = _q("""MATCH (f:File)-[e:CO_CHANGED_WITH]->(o:File) WHERE f.path IN $fs AND NOT o.path IN $fs
                 OPTIONAL MATCH (c:Commit)-[:TOUCHED]->(f)
                 WITH f, o, e.weight AS w, count(DISTINCT c) AS n
                 RETURN f.path, o.path, w, n ORDER BY w DESC LIMIT 80""", {"fs": resolved})
    best = {}
    for f, o, w, n in rows:
        conf = w / n if n else 0
        if w >= MIN_WEIGHT and conf >= MIN_CONF and (o not in best or conf > best[o]["confidence"]):
            best[o] = dict(file=o, because=f, times_together=w, of_commits=n, confidence=round(conf, 2))
    comps = sorted(best.values(), key=lambda c: (-c["confidence"], -c["times_together"]))[:6]
    out["companions"] = comps
    comp_pts = round(40 * min(1.0, sum(c["confidence"] for c in comps[:3]) / 1.5))
    out["factors"].append(dict(name="Files that usually change with these, but not here", points=comp_pts, max=40,
                               detail=f"{len(comps)} companion file(s) missing" if comps else "no frequent companions are missing"))
    for c in comps:
        out["path"].append([c["because"], f"CO_CHANGED_WITH x{c['times_together']}", c["file"]])

    mods = {r[0] for r in _q("MATCH (f:File)-[:IN_MODULE]->(m:Module) WHERE f.path IN $fs RETURN m.name", {"fs": resolved})}
    br_pts = min(10, 5 * max(0, len(mods) - 1))
    out["factors"].append(dict(name="Spans modules", points=br_pts, max=10, detail=f"{len(mods)} module(s): {', '.join(sorted(mods))[:120]}"))
    size_pts = min(15, round(15 * len(files) / 20))
    out["factors"].append(dict(name="Change size", points=size_pts, max=15, detail=f"{len(files)} file(s)"))

    score = crit_pts + comp_pts + br_pts + size_pts
    out["score"] = score
    if base and len(base) >= 30:  # relative to this repo's own history
        pct = round(100 * bisect.bisect_right(base, score) / len(base))
        out["percentile"], out["baseline_n"] = pct, len(base)
        out["verdict"] = "HIGH" if pct >= HIGH_PCT else "MEDIUM" if pct >= MED_PCT else "LOW"
    else:
        out["verdict"] = "HIGH" if score >= 60 else "MEDIUM" if score >= 35 else "LOW"
    if light:
        return out

    rev = tools.suggest_reviewers(resolved, exclude=author)
    out["reviewers"] = rev["data"].get("reviewers", [])
    out["path"] += rev["path"]
    for f in sorted(resolved, key=lambda x: ranked.index(x) if x in ranked else 9999)[:2]:
        w = tools.why(f, 2)
        out["decisions"] += [dict(d, file=f) for d in w["data"].get("decisions", [])]
    top = comps[0] if comps else None
    out["summary"] = (f"{out['verdict']} risk ({score}/100)" + (f", riskier than {out['percentile']}% of this repo's past changes. " if "percentile" in out else ". ") +
                      (f"Usually changes with {top['file'].rsplit('/', 1)[-1]} ({int(top['confidence'] * 100)}% of the time) but it's not in this change. " if top else "") +
                      (f"Suggested reviewer: {out['reviewers'][0]['person']}." if out["reviewers"] else ""))
    return out


if __name__ == "__main__":  # self-check on the demo graph
    CURRENT.set("repo")
    r = analyze(["graphrag_sdk/src/graphrag_sdk/storage/graph_store.py"])
    assert r["verdict"] in ("LOW", "MEDIUM", "HIGH") and r["factors"] and r["reviewers"]
    assert analyze(["no/such/file.py"])["verdict"] == "UNKNOWN"
    print(json.dumps({k: r[k] for k in ("score", "verdict", "summary", "companions")}, indent=1)[:1500])
