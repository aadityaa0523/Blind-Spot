"""Record real agent runs to static/demo.json so the UI can replay them without any LLM call (judges, videos, offline).

Usage: python record_demo.py [graph_name=repo]   (needs a working LLM key; run once, commit the JSON)
"""
import json
import sys
import time

from agent import ask_stream

QUESTIONS = [
    "I'm changing storage/graph_store.py: what else might break and who should review?",
    "What are the most load-bearing files, who knows the top one best, and what changes together with it?",
    "Who knows api/main.py best, and why was it built the way it is?",
]


def main(repo="repo"):
    runs = []
    for q in QUESTIONS:
        t0, events = time.perf_counter(), []
        for ev in ask_stream("demo", q, repo):
            ev = dict(ev, t=round(time.perf_counter() - t0, 2))
            if ev["type"] == "step":
                ev.pop("data", None)  # the UI only needs the one-line summary and path
            events.append(ev)
        runs.append(dict(question=q, repo=repo, events=events))
        print(f"recorded ({events[-1]['t']}s): {q[:60]}")
    json.dump(dict(recorded=time.strftime("%Y-%m-%d"), runs=runs), open("static/demo.json", "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "repo")
