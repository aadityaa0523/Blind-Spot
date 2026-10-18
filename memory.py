"""Agent memory (one isolated FalkorDB graph per user) and the shared multi-agent blackboard.

mem_<user>:  (Episode {question, answer, ts})-[:ABOUT]->(Entity {name})   episodic
             (Fact {text, ts})-[:ABOUT]->(Entity)                          semantic
board:       (Task)-[:HAS_STEP]->(Step {agent, action, summary})           coordination / handoffs
             (Step)-[:NEXT]->(Step)
"""
import time
import uuid

from db import _db, memory

BOARD = _db.select_graph("board")


def remember(user: str, question: str, answer: str, entities: list[str], facts: list[str]):
    g = memory(user)
    ts = int(time.time())
    g.query("CREATE (:Episode {id:$id, question:$q, answer:$a, ts:$ts})",
            {"id": uuid.uuid4().hex[:8], "q": question[:300], "a": answer[:800], "ts": ts})
    for e in entities:
        g.query("MATCH (ep:Episode {ts:$ts, question:$q}) MERGE (n:Entity {name:$e}) MERGE (ep)-[:ABOUT]->(n)",
                {"ts": ts, "q": question[:300], "e": e})
    for text in facts:
        g.query("CREATE (f:Fact {text:$t, ts:$ts})", {"t": text[:300], "ts": ts})
        for e in entities:
            g.query("MATCH (f:Fact {text:$t}),(n:Entity {name:$e}) MERGE (f)-[:ABOUT]->(n)", {"t": text[:300], "e": e})


def recall(user: str, entities: list[str] | None = None, limit: int = 4):
    """Facts (always) + past episodes about the entities in play. Empty graph on first visit is fine."""
    g = memory(user)
    try:
        facts = [r[0] for r in g.ro_query("MATCH (f:Fact) RETURN f.text ORDER BY f.ts DESC LIMIT $l", {"l": limit}).result_set]
        eps = []
        if entities:
            eps = g.ro_query("""MATCH (ep:Episode)-[:ABOUT]->(n:Entity) WHERE n.name IN $es
                                RETURN DISTINCT ep.question, ep.answer, ep.ts ORDER BY ep.ts DESC LIMIT $l""",
                             {"es": entities, "l": limit}).result_set
    except Exception:  # graph doesn't exist yet for a brand-new user
        return {"facts": [], "episodes": []}
    return {"facts": facts, "episodes": [dict(question=r[0], answer=r[1], ts=r[2]) for r in eps]}


def dump(user: str):
    g = memory(user)
    try:
        rows = g.ro_query("MATCH (a)-[r]->(b) RETURN labels(a)[0], coalesce(a.text,a.question,a.name), type(r), "
                          "labels(b)[0], coalesce(b.text,b.question,b.name) LIMIT 80").result_set
    except Exception:
        return []
    return [dict(a=r[1], rel=r[2], b=r[4], a_label=r[0], b_label=r[3]) for r in rows]


class Task:
    """One question = one Task node; each agent appends a Step and hands off to the next."""

    def __init__(self, user: str, question: str):
        self.id = uuid.uuid4().hex[:8]
        self.n = 0
        BOARD.query("CREATE (:Task {id:$id, user:$u, question:$q, ts:$ts})",
                    {"id": self.id, "u": user, "q": question[:300], "ts": int(time.time())})

    def step(self, agent: str, action: str, summary: str):
        self.n += 1
        BOARD.query("""MATCH (t:Task {id:$id}) CREATE (t)-[:HAS_STEP]->(s:Step {n:$n, agent:$a, action:$ac, summary:$s})
                       WITH s MATCH (t:Task {id:$id})-[:HAS_STEP]->(p:Step {n:$pn}) CREATE (p)-[:NEXT]->(s)""",
                    {"id": self.id, "n": self.n, "a": agent, "ac": action, "s": summary[:300], "pn": self.n - 1}) \
            if self.n > 1 else BOARD.query(
                "MATCH (t:Task {id:$id}) CREATE (t)-[:HAS_STEP]->(:Step {n:1, agent:$a, action:$ac, summary:$s})",
                {"id": self.id, "a": agent, "ac": action, "s": summary[:300]})
