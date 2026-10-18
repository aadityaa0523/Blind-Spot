# Deploying Blindspot

One container (`Dockerfile`) plus a FalkorDB instance. On a fresh database the app builds the demo graph itself
(clones FalkorDB/GraphRAG-SDK history, no checkout) and replays 12 bundled real pull requests so the Board and Audit pages are populated.
No GitHub API calls are needed for that.

## Option A: Render + FalkorDB Cloud (about 15 minutes, needs two free accounts)

1. **FalkorDB Cloud**: create a free instance at https://app.falkordb.cloud. Note the host, port, username and password.
2. **GitHub**: push this folder to a public repository (the hackathon requires it).
3. **Render**: New → Blueprint → pick the repository. `render.yaml` defines the service. Fill in:
   `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USER`, `FALKORDB_PASSWORD`.
   `FALKORDB_SSL` defaults to `1` in `render.yaml`; set it to `0` if your instance does not use TLS.
   Optional: `GEMINI_API_KEY` (only the Ask page needs it) and `GITHUB_TOKEN` (raises the GitHub limit for new PR lookups).
4. After the first deploy, open `/api/health`, then `/api/repos` (the demo repo appears within about a minute), then the site.

## Option B: any VM with Docker

```bash
cp .env.example .env        # LLM_MODELS + provider key (optional)
docker compose up -d --build
```

## What has and has not been verified

- Verified locally (without Docker): the app, the 15 tests, the audit chain, the PR cache, rate limits and the CI script.
- **Not verified:** FalkorDB Cloud connection details (TLS, port), Render's free-tier limits (512 MB RAM, sleeps when idle, so the first request after a pause is slow), and the Render blueprint itself.
- **Docker image build and first-start bootstrap on a clean database: not yet confirmed** (a test run was in progress when this was written). Treat `docker compose up` as untested until you have run it.

## Cautions for a public demo

- There is **no authentication**. Per-IP rate limits cover the costly endpoints (Ask 8/min, add a repo 4/min, gate 60/min); behind a proxy set `TRUST_PROXY=1` so the real client IP is used. The Merge gate, Board, Analytics, Audit, Proof and the recorded run need no LLM key.
  The Ask page spends your LLM quota, so leave `GEMINI_API_KEY` unset on a public deployment, or use a key with a spending cap.
- "Add GitHub repo" clones arbitrary public repositories (history only). It runs one job at a time and caps history at 2,000 commits.
- The audit chain detects edits; it is not a signature.
