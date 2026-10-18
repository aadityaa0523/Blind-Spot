"""Audit trail of every merge-gate verdict, stored as a graph in FalkorDB (graph: checks) and hash-chained.

(Check {id, ts, repo, verdict, score, ...})-[:TOUCHED]->(Path)      files the change touched
(Check)-[:MISSING {confidence}]->(Path)                              companions the gate flagged as forgotten
(Check)-[:RECOMMENDED]->(Reviewer)                                   who the gate suggested
Each Check stores sha256(prev_hash + its canonical JSON): editing or deleting an old record breaks every later hash.
This detects edits, it is not a signature (anyone with write access to the database could recompute the chain).
"""
import hashlib
import json
import statistics
import threading
import time
import uuid

from db import _db

LOCK = threading.Lock()
GENESIS = "0" * 64


def g():
    return _db.select_graph("checks")


def _canon(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(prev: str, rec: dict) -> str:
    return hashlib.sha256((prev + _canon(rec)).encode()).hexdigest()


def _last_hash() -> str:
    try:
        rows = g().ro_query("MATCH (c:Check) RETURN c.hash ORDER BY c.seq DESC LIMIT 1").result_set
    except Exception:  # graph doesn't exist yet
        return GENESIS
    return rows[0][0] if rows else GENESIS


def record(result: dict, repo: str) -> str:
    """Persist one gate result; returns the check id."""
    pr = result.get("pr") or {}
    rec = dict(id=uuid.uuid4().hex[:10], ts=int(time.time()), repo=repo, verdict=result["verdict"], score=result["score"],
               n_files=len(result["files"]), pr_number=pr.get("number"), pr_title=(pr.get("title") or "")[:200],
               pr_author=pr.get("author") or "", pr_url=(f"https://github.com/{pr['owner']}/{pr['name']}/pull/{pr['number']}" if pr else ""),
               analysis_ms=result.get("analysis_ms", 0))
    body = json.dumps({k: result[k] for k in ("files", "known", "new_or_unknown", "factors", "companions", "reviewers", "decisions", "summary", "path")
                       if k in result}, ensure_ascii=True)
    with LOCK:  # the chain is linear, so writers take turns
        prev = _last_hash()
        h = _digest(prev, dict(rec, body=hashlib.sha256(body.encode()).hexdigest()))
        seq = (g().query("MATCH (c:Check) RETURN count(c)").result_set[0][0]) if prev != GENESIS else 0
        g().query("CREATE (:Check {id:$id, ts:$ts, seq:$seq, repo:$repo, verdict:$v, score:$s, n_files:$nf, pr_number:$pn, pr_title:$pt, "
                  "pr_author:$pa, pr_url:$pu, analysis_ms:$ms, body:$body, prev:$prev, hash:$h})",
                  dict(id=rec["id"], ts=rec["ts"], seq=seq, repo=repo, v=rec["verdict"], s=rec["score"] if rec["score"] is not None else -1, nf=rec["n_files"],
                       pn=rec["pr_number"] or 0, pt=rec["pr_title"], pa=rec["pr_author"], pu=rec["pr_url"], ms=rec["analysis_ms"], body=body, prev=prev, h=h))
        for f in result.get("known", [])[:60]:
            g().query("MATCH (c:Check {id:$id}) MERGE (p:Path {key:$k}) SET p.path=$p, p.repo=$r CREATE (c)-[:TOUCHED]->(p)", dict(id=rec["id"], k=repo + "|" + f, p=f, r=repo))
        for c in result.get("companions", []):
            g().query("MATCH (c:Check {id:$id}) MERGE (p:Path {key:$k}) SET p.path=$p, p.repo=$r CREATE (c)-[:MISSING {confidence:$cf}]->(p)",
                      dict(id=rec["id"], k=repo + "|" + c["file"], p=c["file"], r=repo, cf=c["confidence"]))
        for r in result.get("reviewers", []):
            g().query("MATCH (c:Check {id:$id}) MERGE (v:Reviewer {key:$k}) SET v.name=$n CREATE (c)-[:RECOMMENDED]->(v)", dict(id=rec["id"], k=repo + "|" + r["person"], n=r["person"]))
    return rec["id"]


def _row(r):
    return dict(id=r[0], ts=r[1], repo=r[2], verdict=r[3], score=None if r[4] == -1 else r[4], n_files=r[5], pr_number=r[6] or None,
                pr_title=r[7], pr_author=r[8], pr_url=r[9], analysis_ms=r[10], hash=r[11], prev=r[12], seq=r[13])


COLS = "c.id, c.ts, c.repo, c.verdict, c.score, c.n_files, c.pr_number, c.pr_title, c.pr_author, c.pr_url, c.analysis_ms, c.hash, c.prev, c.seq"


def recent(limit=50, verdict=None, repo=None):
    where, params = [], {"l": limit}
    if verdict:
        where.append("c.verdict = $v"); params["v"] = verdict
    if repo:
        where.append("c.repo = $r"); params["r"] = repo
    try:
        rows = g().ro_query(f"MATCH (c:Check) {'WHERE ' + ' AND '.join(where) if where else ''} RETURN {COLS} ORDER BY c.seq DESC LIMIT $l", params).result_set
    except Exception:
        return []
    return [_row(r) for r in rows]


def get(check_id: str):
    try:
        rows = g().ro_query(f"MATCH (c:Check {{id:$id}}) RETURN {COLS}, c.body", {"id": check_id}).result_set
    except Exception:
        return None
    if not rows:
        return None
    return dict(_row(rows[0]), result=json.loads(rows[0][14]))


def verify():
    """Recompute the whole chain. Returns {ok, checked, broken_at}. Detects edited or removed records."""
    try:
        rows = g().ro_query(f"MATCH (c:Check) RETURN {COLS}, c.body ORDER BY c.seq ASC").result_set
    except Exception:
        return dict(ok=True, checked=0, broken_at=None)
    prev = GENESIS
    for i, r in enumerate(rows):
        row = _row(r)
        rec = dict(id=row["id"], ts=row["ts"], repo=row["repo"], verdict=row["verdict"], score=row["score"], n_files=row["n_files"],
                   pr_number=row["pr_number"], pr_title=row["pr_title"], pr_author=row["pr_author"], pr_url=row["pr_url"], analysis_ms=row["analysis_ms"])
        want = _digest(prev, dict(rec, body=hashlib.sha256(r[14].encode()).hexdigest()))
        if row["prev"] != prev or row["hash"] != want:
            return dict(ok=False, checked=i, broken_at=row["id"])
        prev = row["hash"]
    return dict(ok=True, checked=len(rows), broken_at=None)


def analytics(repo=None):
    q = lambda c, p=None: g().ro_query(c, p or {}).result_set
    flt = "WHERE c.repo = $r" if repo else ""
    p = {"r": repo} if repo else {}
    try:
        dist = {v: n for v, n in q(f"MATCH (c:Check) {flt} RETURN c.verdict, count(c)", p)}
        ms = sorted(m for (m,) in q(f"MATCH (c:Check) {flt} RETURN c.analysis_ms", p))
        scores = [s for (s,) in q(f"MATCH (c:Check) {flt} RETURN c.score", p) if s >= 0]
        andr = "AND" if repo else "WHERE"
        flagged = q(f"MATCH (c:Check)-[:TOUCHED]->(x:Path) {flt} {andr if repo else 'WHERE'} c.verdict IN ['HIGH','MEDIUM'] "
                    f"RETURN x.path, count(c) AS n ORDER BY n DESC LIMIT 8", p)
        missing = q(f"MATCH (c:Check)-[m:MISSING]->(x:Path) {flt} RETURN x.path, count(c) AS n, avg(m.confidence) ORDER BY n DESC LIMIT 8", p)
        reviewers = q(f"MATCH (c:Check)-[:RECOMMENDED]->(v:Reviewer) {flt} RETURN v.name, count(c) AS n ORDER BY n DESC LIMIT 8", p)
        repos = q("MATCH (c:Check) RETURN c.repo, count(c) ORDER BY count(c) DESC")
    except Exception:
        return dict(total=0)
    total = sum(dist.values())
    return dict(total=total, verdicts=dist, median_ms=statistics.median(ms) if ms else None, p95_ms=ms[int(0.95 * (len(ms) - 1))] if ms else None,
                mean_score=round(sum(scores) / len(scores), 1) if scores else None,
                most_flagged_files=[dict(path=a, checks=b) for a, b in flagged],
                most_forgotten=[dict(path=a, times=b, avg_confidence=round(c, 2)) for a, b, c in missing],
                reviewer_load=[dict(name=a, checks=b) for a, b in reviewers], repos=[dict(repo=a, checks=b) for a, b in repos])
