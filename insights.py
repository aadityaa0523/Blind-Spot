"""Repo-level insights computed from the graph: who holds the knowledge, and which files behave as one unit.

knowledge(g): bus factor per module + files with a single dominant author + a suggested CODEOWNERS file.
clusters(g):  communities in the co-change graph (FalkorDB label propagation), i.e. "hidden modules".
Both take a FalkorDB graph handle so they can be tested on a scratch graph.
"""
from collections import Counter, defaultdict

MIN_FILE_COMMITS = 5     # ignore files with too little history to say anything about ownership
DOMINANT_SHARE = 0.9     # one person wrote at least this share of the commits touching the file


def knowledge(g, min_commits=MIN_FILE_COMMITS):
    rows = g.ro_query("MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f:File)-[:IN_MODULE]->(m:Module) "
                      "RETURN f.path, m.name, p.name, count(c)").result_set
    per_file, per_mod, file_mod = defaultdict(Counter), defaultdict(Counter), {}
    for path, mod, person, n in rows:
        per_file[path][person] += n
        per_mod[mod][person] += n
        file_mod[path] = mod

    files = []
    for path, authors in per_file.items():
        total = sum(authors.values())
        owner, top = authors.most_common(1)[0]
        if total >= min_commits and top / total >= DOMINANT_SHARE:
            files.append(dict(path=path, owner=owner, commits=total, share=round(top / total, 2), module=file_mod[path]))
    files.sort(key=lambda f: -f["commits"])

    modules = []
    for mod, authors in per_mod.items():
        total, running, bus = sum(authors.values()), 0, 0
        for _, n in authors.most_common():
            running += n
            bus += 1
            if running / total >= 0.5:  # fewest people who together wrote half of the module's history
                break
        modules.append(dict(module=mod, commits=total, bus_factor=bus, people=len(authors),
                            owners=[dict(name=a, share=round(n / total, 2)) for a, n in authors.most_common(3)]))
    modules.sort(key=lambda m: (m["bus_factor"], -m["commits"]))

    lines = ["# Suggested owners from commit history. Replace each name with a GitHub handle before using this file."]
    for m in sorted(modules, key=lambda m: m["module"]):
        if m["module"] != "(root)" and m["commits"] >= min_commits:
            names = [o["name"] for o in m["owners"] if o["share"] >= 0.25] or [m["owners"][0]["name"]]
            lines.append(f"/{m['module']}/  " + " ".join(f"@{n.replace(' ', '-')}" for n in names))
    return dict(single_owner_files=files[:10], modules=modules[:12], codeowners="\n".join(lines) + "\n")


def clusters(g, min_size=3, max_size=30):
    """Files that change as a unit. Skips the giant component (hub files link everything) and singletons."""
    rows = g.ro_query("CALL algo.labelPropagation({nodeLabels:['File'], relationshipTypes:['CO_CHANGED_WITH']}) YIELD node, communityId "
                      "MATCH (node)-[:IN_MODULE]->(m:Module) RETURN communityId, node.path, m.name").result_set
    groups = defaultdict(list)
    for cid, path, mod in rows:
        groups[cid].append((path, mod))
    out = []
    for cid, members in groups.items():
        if not min_size <= len(members) <= max_size:
            continue
        mods = sorted({m for _, m in members})
        out.append(dict(size=len(members), modules=mods, cross_folder=len(mods) > 1, files=sorted(p for p, _ in members)))
    out.sort(key=lambda c: (-c["cross_folder"], -c["size"]))
    return out[:8]
