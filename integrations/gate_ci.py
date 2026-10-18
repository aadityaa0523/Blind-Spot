#!/usr/bin/env python3
"""Ask a Blindspot server for a merge-gate verdict on a PR; print Markdown; fail the build above a threshold.

Standard library only, so it runs on any CI runner.
  python gate_ci.py --url https://your-blindspot.example --pr https://github.com/owner/name/pull/123 --fail-on HIGH --markdown comment.md
Exit codes: 0 ok, 1 verdict at or above --fail-on, 2 could not get a verdict (the build is NOT failed by default; pass --strict to fail).
The repository must already be ingested on the server, and must be public (the server reads the PR from GitHub).
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def comment(d):
    L = [f"### Blindspot: {d['verdict']} risk" + (f" ({d['score']}/100)" if d.get("score") is not None else ""), "", d["summary"], ""]
    if d.get("companions"):
        L += ["**Probably forgotten**", ""]
        L += [f"- `{c['file']}`: changes with `{c['because'].rsplit('/', 1)[-1]}` in {c['times_together']} of {c['of_commits']} commits ({round(c['confidence'] * 100)}%)" for c in d["companions"][:5]] + [""]
    if d.get("reviewers"):
        L += ["**Suggested reviewers:** " + ", ".join(r["person"] for r in d["reviewers"][:3]), ""]
    L.append("<sub>Computed from a FalkorDB graph of this repository's history. No LLM.</sub>")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="base URL of the Blindspot server")
    p.add_argument("--pr", required=True, help="https://github.com/owner/name/pull/N")
    p.add_argument("--fail-on", default="HIGH", choices=["MEDIUM", "HIGH", "NEVER"])
    p.add_argument("--markdown", help="write the PR comment to this file")
    p.add_argument("--strict", action="store_true", help="also fail (exit 2) when no verdict could be obtained")
    a = p.parse_args(argv)
    req = urllib.request.Request(a.url.rstrip("/") + "/api/gate", json.dumps({"pr_url": a.pr}).encode(), {"Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        print(f"Blindspot could not judge this PR: HTTP {e.code} {e.read().decode()[:200]}", file=sys.stderr)
        return 2 if a.strict else 0
    except Exception as e:
        print(f"Blindspot unreachable: {e}", file=sys.stderr)
        return 2 if a.strict else 0
    md = comment(d)
    print(md)
    if a.markdown:
        open(a.markdown, "w", encoding="utf-8").write(md)
    if a.fail_on != "NEVER" and ORDER.get(d["verdict"], -1) >= ORDER[a.fail_on]:
        print(f"::error::Blindspot verdict {d['verdict']} is at or above {a.fail_on}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
