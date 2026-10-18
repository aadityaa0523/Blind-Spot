"""New agent tools (topic search, bus factor, hidden modules) and procedural memory."""
import pytest

import memory as mem
import tools
from db import _db
from tools import CURRENT


def test_new_tools_are_registered_for_the_agent_and_have_schemas():
    names = {s["function"]["name"] for s in tools.SCHEMAS}
    assert {"who_knows_topic", "bus_factor", "hidden_modules"} <= names <= set(tools.TOOLS)


def test_topic_search_finds_people_and_files_and_never_guesses():
    CURRENT.set("repo")
    r = tools.who_knows_topic("dedup")
    assert r["ok"] and r["data"]["experts"] and r["data"]["files"] and r["path"]
    assert tools.who_knows_topic("zzzqqqxxx")["data"]["experts"] == []      # not modelled, and it says so
    assert tools.who_knows_topic("ab")["ok"] is False                       # too short to mean anything


def test_bus_factor_and_hidden_modules_return_graph_evidence():
    CURRENT.set("repo")
    b = tools.bus_factor()
    assert b["ok"] and b["data"]["modules"] and b["path"]
    assert all("tests" in m["module"] for m in tools.bus_factor("tests")["data"]["modules"])
    h = tools.hidden_modules()
    assert h["ok"] and h["data"]["clusters"] and all(c["size"] >= 3 for c in h["data"]["clusters"])


def test_question_kinds():
    assert mem.question_kind("I'm changing graph_store.py, who should review?") == "change-review"
    assert mem.question_kind("Who knows api/main.py best?") == "ownership"
    assert mem.question_kind("What are the most load-bearing files?") == "centrality"
    assert mem.question_kind("hello") == "general"


@pytest.fixture()
def scratch_memory(monkeypatch):
    name = "mem_proc_test"
    monkeypatch.setattr(mem, "memory", lambda user: _db.select_graph(name))
    try:
        _db.select_graph(name).delete()
    except Exception:
        pass
    yield
    try:
        _db.select_graph(name).delete()
    except Exception:
        pass


def test_procedural_memory_learns_reuses_and_stays_per_kind(scratch_memory):
    q = "I'm changing x.py, what else might break and who should review?"
    assert mem.procedure_for("u", q) is None                                    # nothing learned yet
    assert mem.learn_procedure("u", q, ["impact", "who_knows", "impact"]) == "change-review"
    p = mem.procedure_for("u", q)
    assert p["kind"] == "change-review" and p["tools"] == ["impact", "who_knows"] and p["uses"] == 1
    mem.learn_procedure("u", "who should review this change?", ["suggest_reviewers", "impact"])  # same kind, newer plan
    p = mem.procedure_for("u", q)
    assert p["tools"] == ["suggest_reviewers", "impact"] and p["uses"] == 2
    assert mem.procedure_for("u", "What are the most load-bearing files?") is None   # a different kind learned nothing
    assert mem.learn_procedure("u", q, []) is None                                   # failed runs teach nothing
