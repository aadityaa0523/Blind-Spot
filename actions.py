"""Agent actions with a human in the loop, stored as nodes in the `checks` graph.

(Action {id, ts, repo, kind, status, verdict, score, files, reviewers, reason, decided})
The agent can only QUEUE an action (status 'pending'); a person approves or dismisses it on the Board.
Nothing here contacts anyone outside this application.
"""
import json
import time
import uuid
from collections import Counter

import checks


def create(repo, kind, verdict, score, files, reviewers, reason=""):
    aid = uuid.uuid4().hex[:10]
    checks.g().query("CREATE (:Action {id:$id, ts:$ts, repo:$repo, kind:$kind, status:'pending', verdict:$v, score:$s, files:$f, reviewers:$r, reason:$why, decided:0})",
                     dict(id=aid, ts=int(time.time()), repo=repo, kind=kind, v=verdict, s=score if score is not None else -1,
                          f=json.dumps(files[:40]), r=json.dumps(reviewers[:5]), why=reason[:300]))
    return aid


def listing(limit=30):
    try:
        rows = checks.g().ro_query("MATCH (a:Action) RETURN a.id, a.ts, a.repo, a.kind, a.status, a.verdict, a.score, a.files, a.reviewers, a.reason, a.decided, a.triage "
                                   "ORDER BY a.ts DESC LIMIT $l", {"l": limit}).result_set
    except Exception:  # no checks graph yet
        return []
    return [dict(id=r[0], ts=r[1], repo=r[2], kind=r[3], status=r[4], verdict=r[5], score=None if r[6] == -1 else r[6],
                 files=json.loads(r[7]), reviewers=json.loads(r[8]), reason=r[9], decided=r[10] or None, triage=r[11]) for r in rows]


def decide(action_id, decision):
    """Approve or dismiss a PENDING action. Returns False if it does not exist or was already decided."""
    if decision not in ("approved", "dismissed"):
        raise ValueError("decision must be 'approved' or 'dismissed'")
    rows = checks.g().query("MATCH (a:Action {id:$id, status:'pending'}) SET a.status = $s, a.decided = $ts RETURN a.id",
                            {"id": action_id, "s": decision, "ts": int(time.time())}).result_set
    return bool(rows)


# ---- used by the independent triage agent (worker.py) ----
def pending_untriaged():
    return checks.g().ro_query("MATCH (a:Action {status:'pending'}) WHERE a.triaged IS NULL RETURN a.id, a.repo, a.files, a.reviewers ORDER BY a.ts").result_set


def open_load(exclude=""):
    """How many OTHER pending requests name each reviewer."""
    rows = checks.g().ro_query("MATCH (a:Action {status:'pending'}) WHERE a.id <> $x RETURN a.reviewers", {"x": exclude}).result_set
    return Counter(r for (rv,) in rows for r in json.loads(rv))


def set_triage(action_id, reviewers, note):
    checks.g().query("MATCH (a:Action {id:$id}) SET a.reviewers = $r, a.triage = $n, a.triaged = $ts",
                     {"id": action_id, "r": json.dumps(reviewers[:5]), "n": note[:300], "ts": int(time.time())})
