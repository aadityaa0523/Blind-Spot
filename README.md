# Blindspot: never merge blind

**Paste a pull request. Blindspot reads the repository's memory as a graph and tells you what the change usually drags
along, who should review it, and why the code looks the way it does.**

Built for the WeMakeDevs × [FalkorDB](https://www.falkordb.com) *Graph Hacks: Context for AI Agents* hackathon.
The demo runs on the real history of [FalkorDB/GraphRAG-SDK](https://github.com/FalkorDB/GraphRAG-SDK)
(513 commits, 204 files, 11 contributors); any public GitHub repo can be added in the UI in about 30 seconds.

**Tracks entered:** 1 (Agents That Act on Connected Data) · 2 (Agent Memory & Coordination) · 3 (Company Brain)

## What it does

| Page | What you get |
|---|---|
| **Merge gate** | Paste a PR URL. A risk score (0-100) with every point explained, and a verdict relative to the repo's own history ("riskier than 91% of past changes"), the files that *usually change with this one but aren't in the PR* (with confidence), recommended reviewers, the commits that explain *why* the code looks like this, and the evidence graph. **No LLM**: graph queries only, so it is instant and repeatable. |
| **Board / Analytics / Audit** | Every verdict is stored in a `checks` graph. The **board** lists them with filters and permalinks (`#check/<id>`); **analytics** shows verdict mix, files most often flagged, reviewer load and timing; the **audit log** is hash-chained so an edited or removed record is detected (`Verify the chain`). A report can be downloaded as Markdown. |
| **Ask** | A four-agent crew (Memory Keeper → Investigator → Writer → Memory Keeper) answers questions with six fixed graph tools. Every step streams live with its query time; answers are rejected if they name a file that isn't in the evidence. One isolated memory graph per user. |
| **Proof** | A walk-forward backtest computed live from the repo's own history, the scoring formula, and the known gaps. |

Also: an **MCP server** exposing the same graph tools (`mcp_server.py`), and a **recorded-run replay** on the Ask page that
needs no LLM key or quota.

| Track | How it is covered |
|---|---|
| 1. Acts on connected data | Multi-hop traversals, PageRank, shortest path as tools; the gate *decides* (verdict, reviewers) from what the graph shows; MCP tool exposure; explainable evidence on every result. |
| 2. Memory & coordination | `mem_<user>` graph per user (episodes + facts, recalled in later answers); agents hand off through a shared `board` graph. |
| 3. Company brain | People, commits, PRs, files and modules in one graph; "why" traced to the commit/PR that explains it. |

**Integrations** (`#integrate` page): a dependency-free CI script (`integrations/gate_ci.py`) and GitHub Actions workflow that comment the
verdict on every PR and fail the check on HIGH; a plain HTTP API; and the MCP server. **Hardening:** per-IP rate limits on costly endpoints,
security headers, lazy-loaded LLM library (server starts in seconds), PR lookups cached on disk, a bundled seed of real PRs so a fresh deployment
has a populated board, and `render.yaml` + `Dockerfile` for hosting (see `DEPLOY.md`).

Extras: **light and dark themes** (toggle in the header or press `t`; follows your system until you choose; both pass WCAG AA contrast on every page, checked by script), keyboard shortcuts (`?` for help, `g b` board, `/` check a PR), "copy as PR comment", and PR lookups cached on disk
(merged PRs never change, so the 60 requests/hour anonymous GitHub limit is rarely hit).

## Does it work? (measured)

`python backtest.py` replays history commit by commit. For each commit touching 3-10 files it hides one file, shows the
gate the rest using **only co-change statistics from earlier commits**, and checks whether the hidden file is in the
gate's top-3 "probably forgotten" files. Baseline: the 3 most-changed files so far.

| Repo | Trials | Gate recall@3 | Baseline | Lift |
|---|---|---|---|---|
| FalkorDB/GraphRAG-SDK (513 commits) | 783 | 35% | 14% | ×2.4 |
| FalkorDB/falkordb-py (330 commits) | 113 | 20% | 18% | ×1.2 |

Read this honestly: the signal is useful on a large, active repo and **weak on a small one**. Trials from the same commit
are not independent, and tests/docs co-change often, which helps. An earlier leave-one-out version of this test gave a
higher number because it let the gate see future commits; it was replaced by the walk-forward version above.
`eval.py` separately checks that graph answers equal ground truth from `git` itself (top expert and co-change counts: 10/10 and 10/10 on both repos).

## Why a graph

"What else breaks if I change `graph_store.py`, who understands it, and why is it built this way?" spans people,
commits, files and PRs. Retrieval over text chunks cannot follow *who changed what alongside what*. A traversal can,
and it returns the path as evidence.

## Architecture

```
question ─► MemoryKeeper ─► Investigator ─► Writer ─► MemoryKeeper
            (reads mem_<user>)  (picks graph tools)  (answers from evidence only)  (writes episode + fact back)
                         every step is also written to the `board` graph as Task -[:HAS_STEP]-> Step -[:NEXT]-> Step
```

* **Fixed tool catalogue, no generated Cypher.** The model chooses a tool and fills typed arguments
  ([`tools.py`](tools.py)). Model output is never concatenated into a query, so there is no injection surface.
* **Names are resolved against the graph.** `resolve_file()` validates every file name; ambiguous or unknown names
  return candidates instead of a guess.
* **Grounded answers.** The Writer may only name files present in the tool evidence. If it names anything else the
  answer is withheld and replaced with the raw evidence ([`agent.py`](agent.py)).
* **"No result" is not "safe".** Empty traversals are reported as *not modelled*.

## Graph data model

Graph `repo` (shared knowledge):

```
(Person {name})-[:AUTHORED]->(Commit {hash, date, subject, body})
(Commit)-[:TOUCHED]->(File {path})
(Commit)-[:PART_OF]->(PR {number})
(File)-[:IN_MODULE]->(Module {name})
(Commit {seq})                                    // seq = position in history (used by the walk-forward backtest)
(File)-[:CO_CHANGED_WITH {weight}]->(File)      // derived: files changed in the same commit >= 3 times (both directions)
```

Graph `mem_<user>` (one per user, FalkorDB multi-graph tenancy):

```
(Episode {question, answer, ts})-[:ABOUT]->(Entity {name})    // episodic
(Fact {text, ts})-[:ABOUT]->(Entity)                          // semantic
```

Graph `checks` (audit trail, hash-chained):

```
(Check {id, ts, seq, repo, verdict, score, hash, prev, body})-[:TOUCHED]->(Path)
(Check)-[:MISSING {confidence}]->(Path)       // companions flagged as forgotten
(Check)-[:RECOMMENDED]->(Reviewer)
// hash = sha256(prev + canonical JSON of the record): editing or deleting a record breaks every later hash
```

Graph `board` (agent coordination):

```
(Task {id, user, question})-[:HAS_STEP]->(Step {n, agent, action, summary}),  (Step)-[:NEXT]->(Step)
```

## Cypher the product depends on

```cypher
// who knows a file                                              (tools.who_knows)
MATCH (a:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(:File {path:$p})
RETURN a.name, count(c) AS commits, max(c.date) AS last ORDER BY commits DESC LIMIT 5

// blast radius                                                  (tools.impact)
MATCH (:File {path:$p})-[e:CO_CHANGED_WITH]->(o:File)-[:IN_MODULE]->(m:Module)
RETURN o.path, e.weight, m.name ORDER BY e.weight DESC LIMIT $l

// why: commits with rationale in their body                     (tools.why)
MATCH (a:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(:File {path:$p}) WHERE size(c.body) > 120
OPTIONAL MATCH (c)-[:PART_OF]->(pr:PR)
RETURN c.hash, c.date, c.subject, c.body, a.name, pr.number ORDER BY c.date DESC LIMIT $l

// load-bearing files: PageRank over co-change                   (tools.load_bearing)
CALL algo.pageRank('File','CO_CHANGED_WITH') YIELD node, score
RETURN node.path, score ORDER BY score DESC LIMIT $l

// how two files connect: shortest path                          (tools.path_between)
MATCH (x:File {path:$a}),(y:File {path:$b})
WITH shortestPath((x)-[:CO_CHANGED_WITH*..8]->(y)) AS p RETURN [n IN nodes(p) | n.path], length(p)

// ACTION: pick reviewers for a change                           (tools.suggest_reviewers)
MATCH (a:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f:File) WHERE f.path IN $fs AND a.name <> $ex
RETURN a.name, count(c), count(DISTINCT f) AS files, max(c.date) ORDER BY files DESC, count(c) DESC LIMIT 3
```

## Run it

Needs Docker, an LLM API key, and (for the non-Docker path) Python 3.11+ and git.

**One command (Docker):**

```bash
cp .env.example .env     # set LLM_MODELS and the matching provider key (any litellm model string)
docker compose up --build   # FalkorDB + app; the demo graph is built on first start
# app: http://localhost:8000   FalkorDB Browser: http://localhost:3000
```

**Without Docker for the app:**

```bash
docker compose up -d falkordb
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app:app --port 8000      # open http://localhost:8000 (demo graph builds on first start)
```

In the UI use **+ Add GitHub repo** to graph any public repository, or from the CLI:
`git clone --no-checkout <url> data/x && python ingest.py data/x <graph_name>`.
`ingest.py` only reads `git log` / `git ls-tree` output; it never executes repository code.

Try: *"I'm changing storage/graph_store.py: what else might break and who should review?"*,
*"Who knows api/main.py best, and why was it built the way it is?"*, *"What are the most load-bearing files?"*.
Click any node in the graph to ask about it; switch the "You are" field to see a separate memory graph.

### Use the tools from any MCP client

```json
{ "mcpServers": { "blindspot": { "command": "python", "args": ["mcp_server.py"], "cwd": "/path/to/blindspot" } } }
```

Tools: `list_repos`, `who_knows`, `impact`, `why`, `load_bearing`, `path_between`, `suggest_reviewers` (each takes an optional `repo`).

## Verification

```bash
pytest -q tests                              # grounding guard, resolver, tools, gate, PR-URL validation, backtest lift, repo isolation, audit-chain tamper detection, MCP round trip (no LLM calls)
python backtest.py repo                      # walk-forward backtest of the gate (numbers above)
python eval.py data/GraphRAG-SDK repo 10     # graph vs ground truth computed from git directly
```

`eval.py` takes the 10 most load-bearing files and checks (a) the top expert per file equals the top author from
`git log`, and (b) the top co-changed file's count equals commits touching both. On GraphRAG-SDK and falkordb-py it
agreed on 10/10 experts and 10/10 co-change counts in both repos. CI (`.github/workflows/ci.yml`) runs both against a FalkorDB service container.

## Limitations (honest)

* Verdict thresholds (HIGH above the 80th percentile of the repo's own past changes, MEDIUM above the 50th) are heuristic, not calibrated against real incidents.
* The seeded board (`python seed_checks.py`) runs the gate on real merged PRs; timestamps are the time of the run, and those PRs' own commits are part of the graph.

* "Decisions" are commits whose messages carry a rationale (body > 120 chars). No issue/PR-comment ingestion yet.
* Co-change is a statistical signal from history, not a static dependency analysis.
* One repository per `repo` graph; memory facts are simple (entity-of-interest and reviewer queries).
* Answer latency is dominated by the LLM provider (2-3 model calls); graph queries take milliseconds. Free-tier model quotas are small: set several models in `LLM_MODELS` as a fallback chain.
* Ingest caps history at the latest 2,000 commits per repo and runs one ingest at a time.

## AI disclosure

Built with the assistance of an AI coding assistant (Claude Code). The author reviewed, ran and is responsible for the code.
