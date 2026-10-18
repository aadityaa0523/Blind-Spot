"""Bus factor, CODEOWNERS, hidden modules and the tenant benchmark, checked on small graphs with known answers."""
import pytest

import bench_tenants
import insights
from db import _db

SCRATCH = "insights_test"


@pytest.fixture()
def g():
    graph = _db.select_graph(SCRATCH)
    try:
        graph.delete()
    except Exception:
        pass
    graph = _db.select_graph(SCRATCH)
    yield graph
    try:
        graph.delete()
    except Exception:
        pass


def _file(g, path, module):
    g.query("MERGE (m:Module {name:$m}) MERGE (f:File {path:$p}) MERGE (f)-[:IN_MODULE]->(m)", dict(m=module, p=path))


def _commits(g, person, path, n):
    for i in range(n):
        g.query("MERGE (p:Person {name:$p}) WITH p MATCH (f:File {path:$f}) CREATE (p)-[:AUTHORED]->(:Commit {hash:$h})-[:TOUCHED]->(f)",
                dict(p=person, f=path, h=f"{person}-{path}-{i}"))


def test_bus_factor_dominant_files_and_codeowners(g):
    _file(g, "m1/a.py", "m1"); _file(g, "m2/b.py", "m2")
    _commits(g, "Ada", "m1/a.py", 9); _commits(g, "Bob", "m1/a.py", 1)          # Ada wrote 90% of a.py: bus factor 1
    for who in ("Cy", "Di", "Ed"):                                                 # three equal authors: two of them cover half
        _commits(g, who, "m2/b.py", 3)
    k = insights.knowledge(g)
    by = {m["module"]: m for m in k["modules"]}
    assert by["m1"]["bus_factor"] == 1 and by["m2"]["bus_factor"] == 2
    assert [f["path"] for f in k["single_owner_files"]] == ["m1/a.py"] and k["single_owner_files"][0]["owner"] == "Ada"
    assert "/m1/  @Ada" in k["codeowners"] and k["codeowners"].startswith("# Suggested owners")


def test_files_with_too_little_history_are_not_called_single_owner(g):
    _file(g, "m1/new.py", "m1")
    _commits(g, "Ada", "m1/new.py", 2)
    assert insights.knowledge(g)["single_owner_files"] == []


def test_two_separate_groups_of_files_become_two_hidden_modules(g):
    for grp, mod in (("x", "mx"), ("y", "my")):
        names = [f"{mod}/{grp}{i}.py" for i in range(4)]
        for n in names:
            _file(g, n, mod)
        for a in names:
            for b in names:
                if a != b:
                    g.query("MATCH (a:File {path:$a}),(b:File {path:$b}) CREATE (a)-[:CO_CHANGED_WITH {weight:5}]->(b)", dict(a=a, b=b))
    _file(g, "lonely/z.py", "lonely")                                              # no co-change edges: must not form a cluster
    cl = insights.clusters(g)
    assert sorted(c["size"] for c in cl) == [4, 4] and not any(c["cross_folder"] for c in cl)


def test_tenant_benchmark_isolates_every_read_and_cleans_up():
    r = bench_tenants.main(tenants=20, reads=120, workers=8, write=False)
    assert r["isolation_violations"] == 0 and r["graphs_in_one_instance"] == 20 and r["p95_ms"] >= r["p50_ms"] > 0
    assert not [n for n in _db.list_graphs() if n.startswith("mem_bench_")]
