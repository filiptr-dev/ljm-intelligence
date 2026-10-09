# agent-browser — the LLM headless-browser sidecar

Standalone FastAPI + Playwright/Chromium service. Exposes the five verbs
the backend's loads agent-loop calls — `navigate`, `read_page`, `scroll`,
`wait`, `finish` — and nothing else. No DB, no vault, no outbound calls
other than the browser's own navigation.

The backend talks here over `AGENT_BROWSER_URL` (set in Settings or env);
missing env means every agent source short-circuits `enabled=False`.

## Run locally

```bash
cd agent-browser
pip install -r requirements.txt
python -m playwright install --with-deps chromium   # once
uvicorn app.service:app --host 127.0.0.1 --port 8080
curl -sfS http://127.0.0.1:8080/health
```

Or via Docker (what runs on Render):

```bash
docker compose up -d agent-browser
curl -sfS http://127.0.0.1:${AGENT_BROWSER_PORT:-8080}/health
```

## Auth

If `AGENT_BROWSER_TOKEN` is set on both sides, every POST must carry a
matching `X-Agent-Token` header; a mismatch is `401`. `/health` stays
open so Render's health check works without the secret.

## Tests

```bash
cd agent-browser
timeout 180 pytest tests/ -q
```

Playwright is stubbed in-test; the suite is fast and offline.
