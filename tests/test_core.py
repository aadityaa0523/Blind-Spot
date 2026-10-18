"""Core behaviour tests. Graph-backed tests need FalkorDB running and `python ingest.py <clone> repo` done (see README)."""
import pytest

import tools
from agent import invented_names
from db import _db
from tools import CURRENT


def test_grounding_guard_rejects_invented_files():
    evidence = [{"data": {"file": "src/a/main.py", "co_changed": [{"file": "src/a/util.py"}]}}]
    assert invented_names("Check main.py and util.py.", evidence, {}, "") == set()
    assert invented_names("Also see secret_backdoor.py", evidence, {}, "") == {"secret_backdoor.py"}
    assert invented_names("You asked about config.yaml", evidence, {}, "is config.yaml ok?") == set()  # user-supplied names are fine


def test_resolver_never_guesses():
    ok, _ = tools.resolve_file("this_file_does_not_exist_xyz.py")
    assert ok is None
    out = tools.who_knows("this_file_does_not_exist_xyz.py")
    assert out["ok"] is False and "error" in out["data"]


def test_tools_return_graph_paths():
    top = tools.load_bearing(3)["data"]["load_bearing"][0]["file"]
    experts = tools.who_knows(top)
    assert experts["ok"] and experts["path"], "every answer must carry its graph evidence"
    assert tools.impact(top)["ok"]
    rev = tools.suggest_reviewers([top])
    assert rev["ok"] and rev["data"]["reviewers"]


def test_repo_context_is_isolated_per_call():
    """Regression: streamed generators resume on other threads, so the repo must be set before each tool call."""
    names = {r[0] for r in _db.select_graph("registry").ro_query("MATCH (r:Repo) RETURN r.name").result_set}
    if len(names) < 2:
        pytest.skip("needs two ingested repos")
    a, b = sorted(names)[:2]
    CURRENT.set(a)
    fa = tools.load_bearing(1)["data"]["load_bearing"][0]["file"]
    CURRENT.set(b)
    fb = tools.load_bearing(1)["data"]["load_bearing"][0]["file"]
    assert fa != fb


def test_gate_explains_its_score_and_never_guesses():
    import gate
    CURRENT.set("repo")
    top = tools.load_bearing(1)["data"]["load_bearing"][0]["file"]
    r = gate.analyze([top])
    assert r["verdict"] in ("LOW", "MEDIUM", "HIGH") and r["score"] == sum(f["points"] for f in r["factors"])
    assert r["reviewers"], "gate must recommend someone to review"
    u = gate.analyze(["definitely/not/in/graph.py"])
    assert u["verdict"] == "UNKNOWN" and u["score"] is None  # no history != safe


def test_pr_url_is_validated_before_any_network_call():
    import gate
    import pytest
    for bad in ["https://evil.com/a/b/pull/1", "https://github.com/a/b/issues/1", "javascript:alert(1)", "https://github.com/a/b/pull/x"]:
        with pytest.raises(ValueError):
            gate.fetch_pr(bad)


def test_walk_forward_backtest_beats_the_popularity_baseline():
    import backtest
    r = backtest.main("repo", verbose=False)
    assert r["trials"] > 300 and r["lift"] > 1.5, r
