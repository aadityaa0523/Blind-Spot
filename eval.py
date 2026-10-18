"""Check the graph tools against ground truth from git itself (no LLM involved).

For the N most load-bearing files:
  * who_knows: top author in the graph == top author by `git log -- <file>` (bots excluded)
  * impact:    the #1 co-changed file's `times_together` == commits that touch both files (<= 15-file commits)
Usage: python eval.py data/GraphRAG-SDK [graph_name=repo] [N=10]
"""
import subprocess
import sys
from collections import Counter

import tools
from ingest import MAX_FILES_PER_COMMIT
from tools import CURRENT


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def main(path, name="repo", n=10):
    CURRENT.set(name)
    files = [r["file"] for r in tools.load_bearing(n)["data"]["load_bearing"]]
    live = set(git(path, "ls-tree", "-r", "HEAD", "--name-only").splitlines())
    RS = chr(0x1e)  # record separator emitted by --format=%x1e
    log = git(path, "log", "--no-merges", "--name-only", "--format=%x1e%an")  # author on the first line of each record
    sets = []
    for block in log.split(RS)[1:]:
        head, _, rest = block.partition(chr(10))
        if head.endswith("[bot]"):  # ingest drops bot commits too
            continue
        fs = [x for x in rest.split(chr(10)) if x in live]
        if 1 < len(fs) <= MAX_FILES_PER_COMMIT:
            sets.append(fs)
    ok_who = ok_impact = checked_impact = 0
    for f in files:
        authors = Counter(a for a in git(path, "log", "--no-merges", "--format=%an", "--", f).splitlines() if not a.endswith("[bot]"))
        top_git = authors.most_common(1)[0][0] if authors else None
        top_graph = tools.who_knows(f)["data"]["experts"][0]["person"]
        ok_who += top_git == top_graph
        co = tools.impact(f, 1)["data"]["co_changed"]
        if co:
            other = co[0]["file"]
            both = sum(1 for fs in sets if f in fs and other in fs)
            checked_impact += 1
            ok_impact += both == co[0]["times_together"]
            if both != co[0]["times_together"]:
                print(f"  impact mismatch {f} vs {other}: graph={co[0]['times_together']} git={both}")
        if top_git != top_graph:
            print(f"  who_knows mismatch {f}: graph={top_graph} git={top_git}")
    print(f"who_knows agrees with git on {ok_who}/{len(files)} files; impact count agrees on {ok_impact}/{checked_impact}")
    return ok_who, len(files), ok_impact, checked_impact


if __name__ == "__main__":
    a = sys.argv
    main(a[1], a[2] if len(a) > 2 else "repo", int(a[3]) if len(a) > 3 else 10)
