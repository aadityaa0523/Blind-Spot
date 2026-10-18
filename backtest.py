"""Walk-forward backtest of the merge gate's 'missing companion' signal.

Commits are replayed in history order. For each commit that touched 3-10 files (after >= MIN_HISTORY earlier commits) we hold one
file out, show the gate the rest using ONLY co-change statistics from earlier commits, and ask: is the held-out file among the
top-3 companions it flags?  Baseline: the 3 most frequently changed files so far that are not already in the change.

Usage: python backtest.py [graph_name=repo]
"""
import sys
from collections import Counter, defaultdict
from itertools import combinations

from db import repo
from gate import MIN_CONF, MIN_WEIGHT

MIN_HISTORY, MAX_PAIR_FILES = 30, 15  # MAX_PAIR_FILES mirrors ingest.MAX_FILES_PER_COMMIT


def main(name="repo", verbose=True):
    g = repo(name)
    rows = g.ro_query("MATCH (c:Commit)-[:TOUCHED]->(f:File) RETURN c.seq, collect(f.path) ORDER BY c.seq").result_set
    n_commits, W = Counter(), defaultdict(Counter)
    trials = flagged = hit = base_hit = 0
    for k, (_, files) in enumerate(rows):
        c = set(files)
        if k >= MIN_HISTORY and 3 <= len(c) <= 10:
            popular = [f for f, _ in n_commits.most_common(13)]
            for h in c:
                touched = c - {h}
                cand = {}
                for f in touched:
                    nf = n_commits[f]
                    for o, w in W[f].items():
                        if o not in touched and nf and w >= MIN_WEIGHT and w / nf >= MIN_CONF:
                            cand[o] = max(cand.get(o, 0), w / nf)
                top3 = [o for o, _ in sorted(cand.items(), key=lambda kv: -kv[1])[:3]]
                trials += 1
                flagged += bool(top3)
                hit += h in top3
                base_hit += h in [p for p in popular if p not in touched][:3]
        for f in c:  # only now does this commit become history
            n_commits[f] += 1
        if len(c) <= MAX_PAIR_FILES:
            for a, b in combinations(sorted(c), 2):
                W[a][b] += 1
                W[b][a] += 1
    res = dict(trials=trials, flagged=flagged, hits=hit, recall_at_3=hit / max(trials, 1), coverage=flagged / max(trials, 1),
               precision_when_flagged=hit / flagged if flagged else 0, baseline_recall_at_3=base_hit / max(trials, 1))
    res["lift"] = res["recall_at_3"] / max(res["baseline_recall_at_3"], 1e-9)
    if verbose:
        print(f"walk-forward backtest on '{name}': {trials} held-out-file trials, history only")
        print(f"  flagged something in {res['coverage']:.0%} of trials; held-out file was in the top 3 in {res['recall_at_3']:.1%} of all trials "
              f"({res['precision_when_flagged']:.0%} of flagged trials)")
        print(f"  baseline (3 most-changed files): {res['baseline_recall_at_3']:.1%}  ->  lift x{res['lift']:.1f}")
    return res


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "repo")
