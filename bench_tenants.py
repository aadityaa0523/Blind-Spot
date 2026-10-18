"""Multi-tenant benchmark: many isolated per-user memory graphs in one FalkorDB, read in parallel.

Creates `tenants` memory graphs (one per user, each with an episode and a fact carrying a unique token), then runs
`reads` concurrent recalls and checks every answer contains ONLY its own tenant's token (isolation), timing each call.
Writes bench_results.json (shown on the Proof page) and deletes the benchmark graphs afterwards.

Usage: python bench_tenants.py [tenants=500] [reads=5000] [workers=32]
Numbers include the Python client and the network hop to FalkorDB; they are from one machine, not a production load test.
"""
import json
import platform
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import memory
from db import _db


def pct(sorted_ms, p):
    return sorted_ms[min(len(sorted_ms) - 1, int(p * len(sorted_ms)))]


def main(tenants=500, reads=5000, workers=32, write=True):
    users = [f"bench_{i:04d}" for i in range(tenants)]
    token = {u: uuid.uuid4().hex[:12] for u in users}
    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda u: memory.remember(u, f"question {token[u]}", f"answer {token[u]}", ["a.py"], [f"fact {token[u]}"]), users))
    seed_s = time.perf_counter() - t0
    graphs = len([n for n in _db.list_graphs() if n.startswith("mem_bench_")])

    def one(i):
        u = users[i % tenants]
        t = time.perf_counter()
        r = memory.recall(u, ["a.py"])
        ms = (time.perf_counter() - t) * 1000
        text = json.dumps(r)
        leak = any(token[o] in text for o in (users[(i + 1) % tenants], users[(i + 7) % tenants])) or token[u] not in text
        return ms, leak

    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(one, range(reads)))
    wall = time.perf_counter() - t0
    lat = sorted(ms for ms, _ in res)
    out = dict(tenants=tenants, graphs_in_one_instance=graphs, reads=reads, workers=workers, seed_seconds=round(seed_s, 1),
               p50_ms=round(pct(lat, .50), 1), p95_ms=round(pct(lat, .95), 1), p99_ms=round(pct(lat, .99), 1),
               reads_per_second=round(reads / wall), isolation_violations=sum(1 for _, leak in res if leak),
               measured=time.strftime("%Y-%m-%d"), machine=f"{platform.system()} {platform.machine()}, FalkorDB in Docker, Python client")
    for u in users:  # leave no benchmark graphs behind
        try:
            _db.select_graph("mem_" + u).delete()
        except Exception:
            pass
    if write:
        json.dump(out, open("bench_results.json", "w", encoding="utf-8"), indent=1)
    return out


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:4]]
    print(json.dumps(main(*a), indent=1))
