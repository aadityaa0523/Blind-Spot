"""Rate limits and security headers on the HTTP layer."""
from fastapi.testclient import TestClient

import app as appmod


def test_rate_limit_blocks_bursts_before_any_work_is_done(monkeypatch):
    monkeypatch.setitem(appmod.RATE, "/api/repos", (2, 60))
    appmod.HITS.clear()
    c = TestClient(appmod.app)
    codes = [c.post("/api/repos", json={"url": "https://evil.example/x/y"}).status_code for _ in range(3)]
    assert codes == [400, 400, 429]  # invalid URLs are rejected, and the third request is throttled
    r = c.post("/api/repos", json={"url": "https://evil.example/x/y"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_security_headers_and_health():
    appmod.HITS.clear()
    r = TestClient(appmod.app).get("/api/health")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
