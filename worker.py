"""Triage agent: a second, independent agent process that shares state with the Ask agent only through FalkorDB.

The Ask agent (inside the web app) queues review requests as `Action` nodes. This worker, a separate OS process, polls those nodes,
checks reviewer workload (how many other open requests each suggested reviewer already has) and rebalances if someone is overloaded,
writing its note and the new reviewer list back to the same node. Rule-based, not an LLM: it is coordination through the graph.

Run:  python worker.py            (polls every 3 s)      python worker.py --once   (one pass, for tests)
"""
import json
import sys
import time

import actions
import tools
from tools import CURRENT

LOAD_LIMIT = 2  # a reviewer with this many OTHER open requests counts as overloaded


def triage_once(load_limit=LOAD_LIMIT):
    done = 0
    for aid, repo, files, reviewers in actions.pending_untriaged():
        reviewers, files = json.loads(reviewers), json.loads(files)
        load = actions.open_load(exclude=aid)
        busy = [r for r in reviewers if load[r] >= load_limit]
        if not busy:
            note = f"Checked reviewer workload: nobody has {load_limit}+ other open requests, so the reviewers stand."
        else:
            CURRENT.set(repo)
            alt = [r["person"] for r in tools.suggest_reviewers(files)["data"].get("reviewers", [])]
            swap = [p for p in alt if p not in busy and p not in reviewers and load[p] < load_limit][:len(busy)]
            if swap:
                reviewers = [r for r in reviewers if r not in busy] + swap
                note = f"Rebalanced: {', '.join(busy)} already had {load_limit}+ other open requests; added {', '.join(swap)} (next best by authorship)."
            else:
                note = f"{', '.join(busy)} already have {load_limit}+ other open requests, but no other reviewer covers these files; left unchanged."
        actions.set_triage(aid, reviewers, note)
        done += 1
    return done


if __name__ == "__main__":
    if "--once" in sys.argv:
        print("triaged", triage_once())
    else:
        print("triage agent running; Ctrl+C to stop")
        while True:
            try:
                triage_once()
            except Exception as e:  # keep the loop alive if the database blips
                print("triage pass failed:", str(e)[:120])
            time.sleep(3)
