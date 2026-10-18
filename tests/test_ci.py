"""The CI helper must fail builds only on the verdicts it was told to, and never break CI because the server is down."""
import io
import json
import sys
import urllib.error

sys.path.insert(0, "integrations")
import gate_ci  # noqa: E402


def _fake(verdict, monkeypatch):
    body = dict(verdict=verdict, score=70, summary="s", companions=[dict(file="a/b.py", because="a/c.py", times_together=4, of_commits=8, confidence=0.5)],
                reviewers=[dict(person="Ada")])
    monkeypatch.setattr(gate_ci.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(body).encode()))


def test_fails_only_at_or_above_threshold(monkeypatch, tmp_path):
    args = ["--url", "http://x", "--pr", "https://github.com/o/r/pull/1"]
    _fake("HIGH", monkeypatch)
    assert gate_ci.main(args + ["--fail-on", "HIGH"]) == 1
    _fake("MEDIUM", monkeypatch)
    assert gate_ci.main(args + ["--fail-on", "HIGH"]) == 0
    assert gate_ci.main(args + ["--fail-on", "MEDIUM"]) == 1
    _fake("HIGH", monkeypatch)
    assert gate_ci.main(args + ["--fail-on", "NEVER"]) == 0
    out = tmp_path / "c.md"
    gate_ci.main(args + ["--markdown", str(out)])
    assert "### Blindspot: HIGH risk (70/100)" in out.read_text() and "`a/b.py`" in out.read_text()


def test_unreachable_server_does_not_break_the_build_unless_strict(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(gate_ci.urllib.request, "urlopen", boom)
    args = ["--url", "http://x", "--pr", "https://github.com/o/r/pull/1"]
    assert gate_ci.main(args) == 0 and gate_ci.main(args + ["--strict"]) == 2
