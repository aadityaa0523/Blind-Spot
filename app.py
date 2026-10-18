import collections
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import checks
import gate
import ingest
import memory
from agent import ask_stream
from dotenv import load_dotenv

from db import _db, repo
from tools import CURRENT, load_bearing

load_dotenv()
app = FastAPI(title="Blindspot")

# Per-client limits on the endpoints that cost real resources: (max requests, window in seconds).
RATE = {"/api/ask": (8, 60), "/api/repos": (4, 60), "/api/gate": (60, 60)}
HITS: dict[tuple[str, str], collections.deque] = collections.defaultdict(collections.deque)


def _client_ip(request) -> str:
    if os.getenv("TRUST_PROXY") == "1" and request.headers.get("x-forwarded-for"):  # only behind a proxy you control
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def guard(request, call_next):
    rule = RATE.get(request.url.path) if request.method == "POST" else None
    if rule:
        limit, window = rule
        q, now = HITS[(_client_ip(request), request.url.path)], time.time()
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            return JSONResponse({"detail": f"Too many requests. Limit is {limit} per {window} seconds."}, status_code=429,
                                headers={"Retry-After": str(int(q[0] + window - now) + 1)})
        q.append(now)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp
CLONES = Path(os.getenv("CLONE_DIR", "data/clones"))  # keep this path short on Windows (MAX_PATH)
GH_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
JOBS: dict[str, dict] = {}
GATE_CACHE: dict[str, tuple[float, dict]] = {}  # pr_url -> (expires_at, response): spares GitHub's 60 req/h anonymous limit
GATE_TTL = 3600
METRICS = dict(gate_calls=0, gate_ms_total=0.0, started=time.time())
PROOF_CACHE: dict[str, dict] = {}
JOB_LOCK = threading.Lock()  # one ingest at a time: keeps the demo box responsive


def repos():
    try:
        rows = _db.select_graph("registry").ro_query(
            "MATCH (r:Repo) RETURN r.name, r.url, r.commits, r.files, r.nodes, r.edges ORDER BY r.ts").result_set
    except Exception:  # registry graph doesn't exist until the first ingest
        return []
    return [dict(name=r[0], url=r[1], commits=r[2], files=r[3], nodes=r[4], edges=r[5]) for r in rows]


def valid_repo(name: str) -> str:
    if name not in {r["name"] for r in repos()}:
        raise HTTPException(404, f"unknown repo '{name}'")
    return name


class Ask(BaseModel):
    user: str = "alice"
    question: str
    repo: str = "repo"


@app.post("/api/ask")
def api_ask(body: Ask):
    name, user, q = valid_repo(body.repo), body.user.strip() or "anon", body.question.strip()

    def events():
        try:
            for ev in ask_stream(user, q, name):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:  # surface provider/graph errors to the UI instead of a dead stream
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)[:300]})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/repos")
def api_repos():
    return repos()


class AddRepo(BaseModel):
    url: str


def _run_job(name, url):
    job = JOBS[name]
    log = lambda m: job["log"].append(m)
    try:
        dest = CLONES / name
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        log("cloning history (no checkout, blobless)...")
        subprocess.run(["git", "-c", "core.longpaths=true", "clone", "-q", "--filter=blob:none", "--no-checkout", url, str(dest)],
                       check=True, capture_output=True, timeout=300)
        ingest.main(str(dest), name=name, url=url, progress=log)
        gate._BASE.pop(name, None)
        if name == "repo":
            threading.Thread(target=_warm_baselines, daemon=True).start()
        job["status"] = "done"
    except Exception as e:
        job.update(status="error", error=str(e)[:300])
    finally:
        JOB_LOCK.release()


@app.post("/api/repos")
def api_add_repo(body: AddRepo):
    m = GH_RE.match(body.url.strip())
    if not m:
        raise HTTPException(400, "Only https://github.com/<owner>/<name> URLs are supported")
    name = re.sub(r"[^a-z0-9_]", "_", f"gh_{m.group(1)}__{m.group(2)}".lower())[:60]
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Another repository is being ingested; try again in a minute")
    JOBS[name] = dict(status="running", log=[], started=time.time())
    threading.Thread(target=_run_job, args=(name, f"https://github.com/{m.group(1)}/{m.group(2)}"), daemon=True).start()
    return dict(name=name)


@app.get("/api/jobs/{name}")
def api_job(name: str):
    if name not in JOBS:
        raise HTTPException(404, "no such job")
    return JOBS[name]


class Gate(BaseModel):
    pr_url: str = ""
    files: list[str] = []
    repo: str = "repo"


@app.post("/api/gate")
def api_gate(body: Gate):
    """Merge gate: a PR URL (or a plain list of files) in, an explainable risk verdict out. No LLM involved."""
    pr = None
    key = body.pr_url.strip()
    if key and key in GATE_CACHE and GATE_CACHE[key][0] > time.time():
        return dict(GATE_CACHE[key][1], cached=True)
    if body.pr_url.strip():
        try:
            pr = gate.fetch_pr(body.pr_url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # network / GitHub rate limit / private repo
            raise HTTPException(502, f"Could not read that PR from GitHub: {str(e)[:160]}")
        url = f"https://github.com/{pr['owner']}/{pr['name']}".lower()
        match = next((r["name"] for r in repos() if (r["url"] or "").lower().rstrip("/") == url), None)
        if not match:
            raise HTTPException(404, f"repo_not_ingested:{pr['owner']}/{pr['name']}")
        name, files = match, pr["files"]
    else:
        name, files = valid_repo(body.repo), [f.strip() for f in body.files if f.strip()][:200]
    if not files:
        raise HTTPException(400, "Give a PR URL or at least one file path")
    CURRENT.set(name)
    t0 = time.perf_counter()
    out = gate.analyze(files, author=(pr or {}).get("author", ""), base=gate.baseline(name))
    ms = round((time.perf_counter() - t0) * 1000)
    METRICS["gate_calls"] += 1
    METRICS["gate_ms_total"] += ms
    res = dict(out, pr=pr, repo=name, analysis_ms=ms, cached=False)
    try:
        res["check_id"] = checks.record(res, name)  # audit trail: every fresh verdict is stored and hash-chained
    except Exception as e:  # the verdict is still returned if the audit write fails
        res["check_id"] = None
        res["audit_error"] = str(e)[:120]
    if key:
        GATE_CACHE[key] = (time.time() + GATE_TTL, res)
    return res


@app.get("/api/checks")
def api_checks(verdict: str = "", repo_name: str = "", limit: int = 60):
    v = verdict.upper() if verdict.upper() in ("LOW", "MEDIUM", "HIGH", "UNKNOWN") else None
    return checks.recent(min(max(limit, 1), 200), v, repo_name or None)


@app.get("/api/checks/{check_id}")
def api_check(check_id: str):
    c = checks.get(check_id)
    if not c:
        raise HTTPException(404, "no such check")
    return c


@app.get("/api/analytics")
def api_analytics(repo_name: str = ""):
    return checks.analytics(repo_name or None)


@app.get("/api/audit/verify")
def api_audit_verify():
    return checks.verify()


@app.get("/api/report")
def api_report(repo_name: str = "repo"):
    """Markdown report for a repository: load-bearing files, what the gate flags most, who gets recommended."""
    from fastapi.responses import PlainTextResponse
    name = valid_repo(repo_name)
    CURRENT.set(name)
    meta = next(r for r in repos() if r["name"] == name)
    an = checks.analytics(name)
    top = [t for t in load_bearing(14)["data"]["load_bearing"] if not gate.is_meta(t["file"])][:8]  # docs/lockfiles are not "load-bearing code"
    proof = api_proof(name)
    lines = [f"# Blindspot report: {meta['url'] or name}", "",
             f"_{meta['commits']} commits, {meta['files']} files, {meta['edges']} relationships in the graph._", "",
             "## Load-bearing files (PageRank over co-change)", ""] + [f"{i}. `{t['file']}`" for i, t in enumerate(top, 1)]
    lines += ["", "## Does the gate work here?", "",
              f"Walk-forward backtest: {proof['recall_at_3']:.0%} of hidden files caught in the top 3 vs {proof['baseline_recall_at_3']:.0%} for guessing "
              f"(x{proof['lift']:.1f}, {proof['trials']} trials)."]
    if an.get("total"):
        lines += ["", f"## Gate activity ({an['total']} checks)", "",
                  "Verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(an["verdicts"].items())),
                  "", "### Files most often flagged as forgotten", ""] + [f"- `{m['path']}`: {m['times']} checks (avg confidence {m['avg_confidence']:.0%})" for m in an["most_forgotten"]]
        lines += ["", "### Recommended reviewers", ""] + [f"- {r['name']}: {r['checks']} checks" for r in an["reviewer_load"]]
    lines += ["", "_Generated from the FalkorDB graph; no LLM involved._", ""]
    return PlainTextResponse(chr(10).join(lines), media_type="text/markdown")


@app.get("/api/summary")
def api_summary():
    """Numbers for the landing page, all computed from the live database."""
    rs = repos()
    n = METRICS["gate_calls"]
    return dict(repos=len(rs), commits=sum(r["commits"] or 0 for r in rs), files=sum(r["files"] or 0 for r in rs),
                nodes=sum(r["nodes"] or 0 for r in rs), edges=sum(r["edges"] or 0 for r in rs),
                gate_calls=n, gate_avg_ms=round(METRICS["gate_ms_total"] / n, 1) if n else None)


@app.get("/api/proof")
def api_proof(repo_name: str = "repo"):
    """Walk-forward backtest of the gate's missing-companion signal, computed live from the repo's history."""
    import backtest
    name = valid_repo(repo_name)
    if name not in PROOF_CACHE:  # history only changes on re-ingest, so cache until restart
        PROOF_CACHE[name] = backtest.main(name, verbose=False)
    return PROOF_CACHE[name]


@app.get("/api/overview")
def overview(repo_name: str = "repo"):
    """Backdrop graph: the most load-bearing files and how they co-change."""
    CURRENT.set(valid_repo(repo_name))
    top = [r["file"] for r in load_bearing(25)["data"]["load_bearing"]]
    rows = repo(repo_name).ro_query("MATCH (a:File)-[e:CO_CHANGED_WITH]->(b:File) WHERE a.path IN $t AND b.path IN $t AND id(a) < id(b) "
                                    "RETURN a.path, b.path, e.weight", {"t": top}).result_set
    return dict(nodes=[dict(id=t) for t in top], links=[dict(source=a, target=b, label=f"x{w}") for a, b, w in rows])


@app.get("/api/memory/{user}")
def api_memory(user: str):
    return memory.dump(user)


@app.get("/api/stats")
def stats(repo_name: str = "repo"):
    g = repo(valid_repo(repo_name))
    r = lambda q: g.ro_query(q).result_set[0][0]
    return dict(nodes=r("MATCH (n) RETURN count(n)"), edges=r("MATCH ()-[e]->() RETURN count(e)"),
                commits=r("MATCH (c:Commit) RETURN count(c)"), files=r("MATCH (f:File) RETURN count(f)"),
                people=r("MATCH (p:Person) RETURN count(p)"))


def _seed_checks():
    """Empty audit trail on a fresh database: replay the bundled real PRs through the gate (no GitHub calls)."""
    if checks.recent(1):
        return
    try:
        bundled = json.load(open("seed/pr_cache.json", encoding="utf-8"))
    except Exception:
        return
    by_url = {(r["url"] or "").lower().rstrip("/"): r["name"] for r in repos()}
    for _, pr in sorted(bundled.items(), key=lambda kv: kv[1]["number"]):
        name = by_url.get(f"https://github.com/{pr['owner']}/{pr['name']}".lower())
        if not name:
            continue
        CURRENT.set(name)
        t0 = time.perf_counter()
        out = gate.analyze(pr["files"], author=pr["author"], base=gate.baseline(name))
        checks.record(dict(out, pr=pr, repo=name, analysis_ms=round((time.perf_counter() - t0) * 1000), cached=False), name)


def _warm_baselines():
    for r in repos():
        try:
            gate.baseline(r["name"])
        except Exception:
            pass
    try:
        _seed_checks()
    except Exception:
        pass


@app.on_event("startup")
def bootstrap_demo_repo():
    threading.Thread(target=_warm_baselines, daemon=True).start()
    if os.getenv("PRELOAD_LLM", "1") == "1":  # set 0 on small hosts: litellm needs several hundred MB
        threading.Thread(target=lambda: __import__("litellm"), daemon=True).start()
    """First start on an empty database: build the demo graph so `docker compose up` just works."""
    if not repos() and JOB_LOCK.acquire(blocking=False):
        JOBS["repo"] = dict(status="running", log=[], started=time.time())
        threading.Thread(target=_run_job, args=("repo", "https://github.com/FalkorDB/GraphRAG-SDK"), daemon=True).start()


@app.get("/api/health")
def health():
    _db.list_graphs()
    return dict(ok=True)


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
