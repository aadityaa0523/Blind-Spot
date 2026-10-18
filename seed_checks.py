"""Populate the audit trail by running the merge gate on real, already-merged pull requests.

The verdicts and timings are real; timestamps are the time of this run (not back-dated).
Uses ~2 GitHub API calls per PR, so keep N small without a GITHUB_TOKEN (60 requests/hour anonymous).

Usage: python seed_checks.py [graph_name=repo] [N=12]
"""
import sys
import time

import checks
import gate
from app import repos
from db import repo as repo_graph
from tools import CURRENT


def main(name="repo", n=12):
    meta = next(r for r in repos() if r["name"] == name)
    nums = [r[0] for r in repo_graph(name).ro_query("MATCH (p:PR)<-[:PART_OF]-(c:Commit) RETURN p.number, max(c.seq) AS s ORDER BY s DESC LIMIT $n", {"n": n * 2}).result_set]
    done = 0
    for num in nums:
        if done >= n:
            break
        try:
            pr = gate.fetch_pr(f"{meta['url']}/pull/{num}")
        except Exception as e:  # issue number, deleted, or rate limited
            print(f"  skip #{num}: {str(e)[:60]}")
            continue
        CURRENT.set(name)
        t0 = time.perf_counter()
        out = gate.analyze(pr["files"], author=pr["author"], base=gate.baseline(name))
        res = dict(out, pr=pr, repo=name, analysis_ms=round((time.perf_counter() - t0) * 1000), cached=False)
        cid = checks.record(res, name)
        print(f"  #{num} {out['verdict']:7} {out['score']} {pr['title'][:50]} -> {cid}")
        done += 1
    print("chain:", checks.verify())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "repo", int(sys.argv[2]) if len(sys.argv) > 2 else 12)
