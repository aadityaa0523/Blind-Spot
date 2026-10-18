"""The agent can only QUEUE actions; a person decides. Runs on a scratch graph so real data is untouched."""
import json

import pytest

import actions
import checks
import tools
from db import _db
from tools import CURRENT

SCRATCH = "actions_test"


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


def test_actions_start_pending_and_only_a_person_can_decide_once(scratch):
    aid = actions.create("repo", "review_request", "HIGH", 88, ["a.py", "b.py"], ["Ada", "Bob"], "risky")
    [a] = actions.listing()
    assert a["status"] == "pending" and a["reviewers"] == ["Ada", "Bob"] and a["files"] == ["a.py", "b.py"] and a["decided"] is None
    assert actions.decide(aid, "approved") is True
    assert actions.listing()[0]["status"] == "approved" and actions.listing()[0]["decided"]
    assert actions.decide(aid, "dismissed") is False            # already decided: cannot be flipped
    with pytest.raises(ValueError):
        actions.decide(aid, "executed")                          # no such decision


def test_agent_tool_queues_only_for_risky_known_changes(scratch):
    CURRENT.set("repo")
    bundled = json.load(open("seed/pr_cache.json", encoding="utf-8"))["falkordb/graphrag-sdk#335"]["files"]
    r = tools.request_review(bundled, "dedup change")
    assert r["ok"] and r["data"]["queued"] is True and r["data"]["reviewers"] and "pending human approval" in r["data"]["status"]
    assert actions.listing()[0]["status"] == "pending"                                  # queued, never auto-approved
    assert tools.request_review(["no/such/file.py"])["ok"] is False                      # unknown files: nothing to judge
    assert len(actions.listing()) == 1                                                  # and nothing was queued for them
    g = tools.gate_change(bundled)
    assert g["ok"] and g["data"]["verdict"] in ("MEDIUM", "HIGH") and g["path"]
