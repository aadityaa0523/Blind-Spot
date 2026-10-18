"""The audit chain must detect edited and removed records. Runs on a scratch graph, never on real checks."""
import pytest

import checks
from db import _db

SCRATCH = "checks_test"


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


def _result(verdict, score, n):
    return dict(verdict=verdict, score=score, files=[f"a{i}.py" for i in range(n)], known=["a0.py"], new_or_unknown=[], factors=[],
                companions=[dict(file="b.py", because="a0.py", times_together=4, of_commits=8, confidence=0.5)], reviewers=[dict(person="Ada")],
                decisions=[], summary="s", path=[], analysis_ms=5, pr=None)


def test_chain_verifies_then_detects_an_edit(scratch):
    ids = [checks.record(_result(v, s, n), "r") for v, s, n in (("LOW", 10, 1), ("HIGH", 90, 4), ("MEDIUM", 50, 2))]
    assert checks.verify() == dict(ok=True, checked=3, broken_at=None)
    assert checks.get(ids[1])["result"]["summary"] == "s"
    # someone quietly downgrades the HIGH verdict directly in the database
    checks.g().query("MATCH (c:Check {id:$id}) SET c.verdict = 'LOW'", {"id": ids[1]})
    v = checks.verify()
    assert v["ok"] is False and v["broken_at"] == ids[1]


def test_chain_detects_a_removed_record(scratch):
    ids = [checks.record(_result("LOW", 5, 1), "r") for _ in range(3)]
    checks.g().query("MATCH (c:Check {id:$id}) DETACH DELETE c", {"id": ids[1]})
    v = checks.verify()
    assert v["ok"] is False and v["broken_at"] == ids[2]


def test_analytics_counts_match_what_was_recorded(scratch):
    for v in ("HIGH", "HIGH", "LOW"):
        checks.record(_result(v, 40, 2), "r")
    a = checks.analytics()
    assert a["total"] == 3 and a["verdicts"] == {"HIGH": 2, "LOW": 1}
    assert a["most_forgotten"][0]["times"] == 3 and a["reviewer_load"][0]["name"] == "Ada"
