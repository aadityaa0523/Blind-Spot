# Submission kit

## Demo video script (about 2:50)

1. **0:00 Hook (15s).** "Every merge is a bet that you remembered everything this change touches. Blindspot remembers for you."
2. **0:15 The gate (60s).** Merge gate page, click **#335 dedup fix**. Point at: the 88/100 HIGH verdict, the score breakdown
   ("every point has a reason, and there is no LLM here"), **Probably forgotten** ("changes together with
   test_merge_invariants.py 50% of the time, but it isn't in this PR"), the reviewer, and the commit quote that explains why.
   Then **#343 tiny dependency bump** to show it does not cry wolf.
3. **1:15 The graph (20s).** Show the evidence graph; open FalkorDB Browser (http://localhost:3000, graph `repo`) and run
   `MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f:File) RETURN p,c,f LIMIT 60`.
3b. **1:25 Board and audit (20s).** Board page: "every verdict is kept"; open one for its permalink and **Copy as PR comment**. Audit page: **Verify the chain**,
   "each record carries the hash of the one before it, so an edit is detectable."
3c. **1:45 Company brain (20s).** Analytics page, **Knowledge risk**: "nearly every module has a bus factor of 1, one person wrote it"; show the hidden modules
   (files across docs, examples and tests that always change together) and press **Copy suggested CODEOWNERS**.
4. **1:35 Proof (25s).** Proof tab: "walk-forward backtest, history only: 35% of forgotten files caught, vs 14% for guessing, and
   we also say it is weak on small repos."
4b. **Proof, second card (10s).** "2,000 isolated memory graphs in one FalkorDB, median 15.7 ms, zero cross-tenant leaks", and say it is one machine, a sanity check.
5. **2:00 Agents and memory (35s).** Ask page, **Watch a recorded run** (or ask live): the crew hands off through the graph, each
   graph query takes milliseconds, the answer is marked *grounded*. Replay a second run: "Last time you asked about..." comes from
   that user's own memory graph.
5b. **2:30 Where it fits (10s).** Integrate page: "one script comments the verdict on every PR and fails the build on HIGH; the same tools are over MCP."
6. **2:40 Close (10s).** "Add any public repo, or use the MCP server from your editor. FalkorDB makes the memory fast enough to run on every PR."

Before recording: run each gate example once, and use **Watch a recorded run** if the LLM quota is low.

## Blog post draft (side prize: best blog post)

### Never merge blind: giving code review a memory with FalkorDB

Every team has the same Slack question: *"I'm touching this file, who should look at it, and what else will I break?"*
The answer is never in one place. It lives in who wrote the code, which files tend to change together, and the commit
message from eight months ago that explains the weird bit. Embedding those into text chunks throws away the part that
matters, the relationships.

**The merge gate.** Paste a pull request URL and you get a risk score where every point has a stated reason, plus the files
that usually change with this one but are missing from the PR ("changes together with `test_merge_invariants.py` in 50% of
its commits, not touched here"), a reviewer, and the commit message that explains why. There is no LLM in that path: it is
graph queries (a PageRank for load-bearing files, co-change edges for companions), so it takes milliseconds and gives the
same answer twice.

**Does it work?** I tested it the hard way: replay the history commit by commit, hide one file from each commit, and ask
the gate to find it using only earlier history. On FalkorDB's GraphRAG-SDK it finds the hidden file in its top three 35% of
the time versus 14% for guessing the most-changed files (about x2.5). On the much smaller falkordb-py it is 20% versus 18%, barely
better. So: useful on a big active repo, weak on a small one, and the app says so on its Proof page. My first version of
this test let the gate see future commits and looked better (48%); I threw it away.

**Blindspot** loads a repository's git history into FalkorDB as a graph and puts a small crew of agents on top.
The demo runs on the real history of FalkorDB/GraphRAG-SDK: 513 commits, 204 files, 11 contributors.

**The model**

People author commits, commits touch files, commits belong to PRs, files live in modules. On top of that I derive one
edge: `CO_CHANGED_WITH`, between files that changed in the same commit at least three times. That single edge makes
blast-radius, PageRank and shortest-path queries meaningful.

**Tools, not generated Cypher**

The agent has nine fixed tools (`who_knows`, `impact`, `why`, `load_bearing`, `path_between`, `suggest_reviewers`, `who_knows_topic`, `bus_factor`, `hidden_modules`),
each a parameterised query I wrote. The model picks tools and fills typed arguments. It never writes a query, so
there is nothing to inject, and every name is resolved against the graph first. If it can't find a file, it says so.

**Answers that show their work**

Each tool returns the graph path behind its result, and the UI draws it. The Writer is only allowed to name files
that appear in the evidence; if it names anything else, the answer is rejected and replaced with the raw rows. And an
empty result is reported as "not modelled", never as "safe".

**Memory and coordination**

Four roles hand off through a shared `board` graph: Memory Keeper reads, Investigator queries, Writer answers,
Memory Keeper writes back. Each user gets their own `mem_<user>` graph, which is the practical payoff of FalkorDB's
multi-graph design: tenant isolation without another deployment.

**What I'd do next**

Ingest PR comments and issues so "why" draws on discussion and not only commit messages, and weight co-change by recency.

**What went wrong (and what I learned)**

Free-tier LLM quotas are per model and small; a four-agent loop burns them fast. Cutting each question to two LLM calls
(one parallel round of tool calls, one write-up) and adding a model fallback chain fixed it. Also: FalkorDB's
`shortestPath` must sit in `WITH`/`RETURN` and doesn't support undirected traversals; I stored co-change edges in both
directions instead.

Code: <your public repo URL>
