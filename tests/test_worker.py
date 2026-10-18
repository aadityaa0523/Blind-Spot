"""Two agents, two processes, one shared graph: the triage worker reads and rewrites what the Ask agent queued."""
import json

import pytest

import actions
import checks
import worker
from db import _db

SCRATCH = "worker_test"


@pytest.fixture()
def scratch(monkeypatch):
    monkeypatch.setattr(checks, "g", lambda: _db.select_graph(SCRATCH))
    try:
        _db.select_graph(SCRATCH).delete()
    except Exception:
        pass
    yield
    try:
        _db.select_graph(SCRATCH).delete()
    except Exception:
        pass


def test_triage_agent_checks_workload_rebalances_and_leaves_a_note(scratch):
    files = json.load(open("seed/pr_cache.json", encoding="utf-8"))["falkordb/graphrag-sdk#335"]["files"]
    for _ in range(3):  # three open requests that all name the same two reviewers
        actions.create("repo", "review_request", "HIGH", 88, files, ["Gal Shubeli", "Naseem Ali"], "x")
    assert worker.triage_once() == 3 and worker.triage_once() == 0          # each is triaged exactly once
    notes = [a["triage"] for a in actions.listing()]
    assert all(notes) and any(n.startswith("Rebalanced") for n in notes)


def test_triage_leaves_reviewers_alone_when_nobody_is_overloaded(scratch):
    actions.create("repo", "review_request", "HIGH", 70, ["a.py"], ["Ada", "Bob"], "x")
    assert worker.triage_once() == 1
    a = actions.listing()[0]
    assert a["reviewers"] == ["Ada", "Bob"] and "nobody has" in a["triage"] and a["status"] == "pending"   # triage never approves
