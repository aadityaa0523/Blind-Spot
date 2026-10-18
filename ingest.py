"""Ingest a public git repo's history into FalkorDB (graph: repo).

Reads only `git log` / `git ls-tree` output (never executes repo code), so no checkout is needed.
Usage: python ingest.py data/GraphRAG-SDK
"""
import re
import subprocess
import sys
from collections import Counter
from itertools import combinations

import time

from db import _db, repo

SEP, FSEP = "\x1e", "\x1f"
PR_RE = re.compile(r"#(\d+)")
MERGE_RE = re.compile(r"^Merge pull request #(\d+)")
MAX_FILES_PER_COMMIT = 15  # bigger commits are refactors/vendoring: they pollute co-change signal
MIN_COCHANGE = 3


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def module_of(f):
    parts = f.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(root)")


def load(path, max_commits):
    live = set(git(path, "ls-tree", "-r", "HEAD", "--name-only").splitlines())
    fmt = SEP + FSEP.join(["%H", "%P", "%an", "%ae", "%aI", "%s", "%b"])
    raw = git(path, "log", "--name-only", f"--max-count={max_commits}", f"--format={fmt}")
    commits = []
    for chunk in raw.split(SEP)[1:]:
        fields = chunk.split(FSEP)
        h, parents, an, ae, at, subj = fields[:6]
        rest = FSEP.join(fields[6:])
        body, _, files = rest.rpartition("\n\n") if "\n\n" in rest else ("", "", rest)
        flist = [x for x in files.strip().splitlines() if x in live]
        commits.append(dict(h=h, parents=parents.split(), an=an, at=at[:10], subj=subj, body=body.strip(), files=flist))
    return commits, live


def main(path, name="repo", url="", max_commits=2000, progress=print):
    commits, live = load(path, max_commits)
    commits = [c for c in commits if not c["an"].endswith("[bot]")]
    # Merge-commit PRs: tag every commit on the merged branch with the PR number.
    branch_pr = {}
    for c in commits:
        m = MERGE_RE.match(c["subj"])
        if m:
            p1, p2 = c["parents"][:2]
            for h in git(path, "rev-list", p2, "--not", p1, "--").split():
                branch_pr[h] = int(m.group(1))
    commits = [c for c in commits if c["files"] or MERGE_RE.match(c["subj"])]
    progress(f"parsed {len(commits)} commits, {len(live)} files")
    g = repo(name)
    g.query("MATCH (n) DETACH DELETE n")
    for label, key in [("Person", "name"), ("File", "path"), ("Module", "name"), ("Commit", "hash"), ("PR", "number")]:
        try:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{key})")
        except Exception:
            pass

    rows = []
    for i, c in enumerate(commits):
        m = MERGE_RE.match(c["subj"])
        refs = sorted({int(x) for x in PR_RE.findall(c["subj"] + " " + c["body"])})
        rows.append(dict(h=c["h"], seq=len(commits) - i, an=c["an"], at=c["at"], subj=c["subj"][:200], body=c["body"][:1500],
                         files=c["files"], mods=sorted({module_of(f) for f in c["files"]}),
                         pr=int(m.group(1)) if m else branch_pr.get(c["h"]) or (
                             int(re.search(r"\(#(\d+)\)", c["subj"]).group(1)) if re.search(r"\(#(\d+)\)", c["subj"]) else None),
                         refs=refs))

    for i in range(0, len(rows), 100):
        g.query("""UNWIND $rows AS r
            MERGE (p:Person {name:r.an})
            CREATE (c:Commit {hash:r.h, seq:r.seq, date:r.at, subject:r.subj, body:r.body})
            CREATE (p)-[:AUTHORED]->(c)
            FOREACH (_ IN CASE WHEN r.pr IS NULL THEN [] ELSE [1] END |
                MERGE (pr:PR {number:r.pr}) CREATE (c)-[:PART_OF]->(pr))
            WITH c, r UNWIND r.files AS f
            MERGE (fi:File {path:f}) CREATE (c)-[:TOUCHED]->(fi)""", dict(rows=rows[i:i + 100]))
    files = [dict(f=f, m=module_of(f)) for f in live]
    for i in range(0, len(files), 200):
        g.query("""UNWIND $fs AS x MERGE (f:File {path:x.f}) MERGE (m:Module {name:x.m})
                   MERGE (f)-[:IN_MODULE]->(m)""", dict(fs=files[i:i + 200]))

    co = Counter()
    for c in commits:
        fs = sorted(set(c["files"]))
        if 1 < len(fs) <= MAX_FILES_PER_COMMIT:
            co.update(combinations(fs, 2))
    pairs = [dict(a=a, b=b, w=w) for (a, b), w in co.items() if w >= MIN_COCHANGE]
    for i in range(0, len(pairs), 200):
        g.query("""UNWIND $ps AS p MATCH (a:File {path:p.a}),(b:File {path:p.b})
                   CREATE (a)-[:CO_CHANGED_WITH {weight:p.w}]->(b), (b)-[:CO_CHANGED_WITH {weight:p.w}]->(a)""",
                dict(ps=pairs[i:i + 200]))
    n = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    e = g.query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
    progress(f"graph {name}: {n} nodes, {e} edges, {len(pairs)} co-change pairs")
    reg = _db.select_graph("registry")
    reg.query("MERGE (r:Repo {name:$n}) SET r.url=$u, r.commits=$c, r.files=$f, r.nodes=$nn, r.edges=$e, r.ts=$t",
              dict(n=name, u=url, c=len(commits), f=len(live), nn=n, e=e, t=int(time.time())))
    return dict(name=name, nodes=n, edges=e, commits=len(commits), files=len(live))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "repo", sys.argv[3] if len(sys.argv) > 3 else "")
