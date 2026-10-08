# Onion / SOLID refactor — pre-flight inventory

**Branch:** `main` (post the 2026-10-08 loads-aggregator batch — SHAs
`0ea4b2a .. b1929b9`). No pull/rebase; the aggregator already landed as
local commits.

**Plan:** [[ljm-intelligence-architecture-onion-solid-plan]]
(2026-10-08-architecture-onion-solid.md, approved — amendments: pipeline
all-into-prospecting; `api/jobs.py` → `integrations/`; no operation_id
pinning).

**Why this doc exists:** the plan says the first commit on this task is
an inventory. Reviewer reads this first; every move below has a
destination; nothing listed here is new feature work.

---

## Legacy top-level folders to delete after fold-in

- `app/api/` — 17 files (see routing table below).
- `app/pipeline/` — 10 files (`run.py`, `broker_next_action.py`,
  `call_rank.py`, `enrichment.py`, `gemini_stage.py`, `match.py`,
  `shipper_ingest.py`, `shipper_match.py`, `shipper_merge.py`,
  `shipper_rank.py`).
- `app/services/` — 2 files (`ai_usage.py`, `unsub_config.py`).
- `app/sources/` — 7 files (`emails.py`, `fetcher.py`,
  `gemini_extractor.py`, `gemini_search.py`, `linkedin_search.py`,
  `osm_overpass.py`, `robots.py`, `site_scraper.py`).
- `app/scoring/` — 1 file (`fit_score.py`).
- `app/lib/` — 2 files (`circuit_breaker.py`, `tokens.py`).

## Shims that stay (one-line docstring)

- `app/models/__init__.py` — compat re-export of per-module `models.py`
  (50+ call sites + Alembic autogenerate uses it).
- `app/auth/__init__.py` — becomes a re-export of `app.identity.auth`.
- `app/db.py` — pragmatic exception; stays at root per §E of the plan.

---

## Routing table — `app/api/*` fold-in

| Legacy file | Destination | Prefix | Notes |
|---|---|---|---|
| `api/auth.py` | `identity/router.py` | `/auth` | Inline `select(User)` → `UserRepo.by_email`. |
| `api/settings.py` | `identity/router.py` | `/settings` | Includes new load-board connector endpoints (shipped 2026-10-08). |
| `api/jobs.py` | `integrations/router.py` | `/jobs`, `/admin/jobs` | Amendment #2 — not `shared/`. |
| `api/leads.py` | `prospecting/router.py` | `/leads` | |
| `api/brokers.py` | `prospecting/router.py` | `/brokers` | |
| `api/shipper_finder.py` | `prospecting/router.py` | `/shippers` | |
| `api/crawl.py` | `prospecting/router.py` | `/crawl` | |
| `api/enrichment.py` | `prospecting/router.py` + `outreach/router.py` | `/enrichment` + `/unsubscribe` | `unsub_router` splits off to outreach. |
| `api/call_list.py` | `outreach/router.py` | `/call-list` | |
| `api/capacity.py` | `outreach/router.py` | `/capacity` | |
| `api/email.py` | `outreach/router.py` | `/email` | |
| `api/mail.py` | `inbox/router.py` | `/mail`, `/cron/mail-*` | |
| `api/ai.py` | `analysis/router.py` | `/ai` | |
| `api/overview.py` | `analysis/router.py` | `/overview` | |
| `api/loads.py` | `integrations/router.py` | `/loads` | Post-aggregator shape; `_store_batch`/`list_loads_deduped`/`set_group_status`/`group_contact` already in `integrations/loads_service.py`. |
| `api/_auth.py` | `shared/cron_auth.py` | n/a | `check_secret` + signing helpers. |

## Legacy folders — fold-in

| Folder | Destination |
|---|---|
| `app/pipeline/*` | `prospecting/pipeline/` (amendment #1 — no split of `enrichment.py`). |
| `app/services/ai_usage.py` | `analysis/ai_usage_service.py`. |
| `app/services/unsub_config.py` | `outreach/unsub_config.py`. |
| `app/sources/{gemini_search,gemini_extractor,linkedin_search}.py` | `integrations/adapters/ai/`. |
| `app/sources/{site_scraper,osm_overpass,emails}.py` | `integrations/adapters/web/`. |
| `app/sources/{fetcher,robots}.py` | `shared/http/`. |
| `app/scoring/fit_score.py` | `prospecting/scoring.py`. |
| `app/auth/{deps,tokens,passwords}.py` | `identity/auth/`. |
| `app/lib/circuit_breaker.py` | `shared/circuit_breaker.py`. |
| `app/lib/tokens.py` | `shared/tokens.py` (used by auth + unsub). |

## Per-module `repository.py` additions

| Module | Aggregates / Repos |
|---|---|
| `identity` | `UserRepo`, `SettingsRepo`, `TenantCredentialRepo`, `TenantFeatureFlagRepo`. |
| `integrations` | `LoadRepo`, `AgentRunRepo`, `JobRepo`. |
| `prospecting` | `LeadRepo`, `LeadContactRepo`, `CrawlRunRepo`, `BrokerPredictionRepo`, `EnrichmentCandidateRepo`, `FitScoreHistoryRepo`. |
| `outreach` | `SuppressionRepo`, `EmailDraftRepo`, `CampaignRepo`, `OutreachLogRepo`. |
| `inbox` | `MailMessageRepo`, `MessageInsightRepo`, `NoReplyTrackerRepo`, `MailCursorRepo`. |
| `analysis` | `BrokerPredictionRepo` (read side), `LanePredictionRepo`, `PredictionRunRepo`, `AiUsageLogRepo`. |

## Fat-service split targets

- `analysis/kpi_service.py` — 887 LOC → `analysis/service.py` (<400) +
  `analysis/repository.py` + `analysis/schemas.py` (DTOs). Keep public
  function names so the router keeps importing the same symbols.
- `inbox/service.py` — 896 LOC → `inbox/service.py` (<400) +
  `inbox/repository.py` + `inbox/schemas.py`. Pulls queries out; keeps
  triage/orchestration.

## Import-linter contracts (new)

5. Services never import `sqlalchemy.sql.expression`.
6. Routers never import a module's `repository`.
7. Routers never import `app.models`.
8. Nothing imports from `app.pipeline`, `app.services`, `app.sources`,
   `app.scoring`, `app.lib`, `app.api` (deleted packages become tripwires).

## OpenAPI diff gate

Captured on the pre-refactor tip (`b1929b9`) and the post-refactor tip —
must be byte-identical. Amendment #3: no `operation_id=` pinning;
function names + decorator paths + `tags=` identical during moves.

## Risks

- **Loads aggregator shipped on `main`.** New files
  (`agent_browser/`, `loads_extract.py`, migrations 0025+0026, settings
  endpoints) are included in this inventory and will be moved per
  the above table. Not a rebase conflict since the aggregator work
  landed as the preceding commits on this same branch.
- **12h scope.** Any "while we're here" cleanup is a new todo, not a
  commit on this branch (plan §J risk #5).

---

**Status:** inventory only — no code moved yet. Reviewer approves this
doc before the fold-in commits start.
