import os
from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()
_db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", "localhost"),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    username=os.getenv("FALKORDB_USER") or None,
    password=os.getenv("FALKORDB_PASSWORD") or None,
    ssl=os.getenv("FALKORDB_SSL", "") == "1",  # hosted FalkorDB usually needs TLS
)


def repo(name: str = "repo"):
    return _db.select_graph(name)


def memory(user: str):
    # One isolated graph per user: FalkorDB multi-graph tenancy.
    safe = "".join(c for c in user.lower() if c.isalnum() or c == "_")[:32] or "anon"
    return _db.select_graph(f"mem_{safe}")


def list_graphs():
    return _db.list_graphs()
