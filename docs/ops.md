# Operator playbook — LJM Intelligence

## 1. Connect Gmail Workspace

**Prereq (LJM-admin side).**
1. In Google Workspace admin, create a service account with domain-wide delegation.
2. Grant the SA the OAuth scopes:
   - `https://www.googleapis.com/auth/gmail.readonly` (read)
   - `https://www.googleapis.com/auth/gmail.send` (send)
   - `https://www.googleapis.com/auth/admin.directory.user.readonly` (mailbox list)
3. In Workspace admin → Security → API controls → Domain-wide Delegation, add the SA's
   client ID with the three scopes above.
4. Download the SA JSON key.

**In the app.**
1. Sign in as the owner.
2. Settings → Owner switches → "Gmail connector" → paste the SA JSON + an impersonate
   mailbox (e.g. `owner@ljminternational.com`) → **Store credential**.
   This writes an AES-GCM record into `tenant_credentials` via `CredentialVault`.
3. Settings → Owner switches → **Inbox: gmail**. Reads now come from real Gmail.
4. Settings → Owner switches → type `CONFIRM` → **Send via Gmail: ON** to actually send.
   The default is OFF; while OFF, "Send" writes to `SimulatedSender` and records a
   receipt in `sent_log`.

The domain is **`ljminternational.com`**.

## 2. Forget a contact (GDPR-style)

Settings → Owner switches → **Forget contact** → type the full address.
Every `mail_messages` + `message_insights` + `no_reply_tracker` row for that
address is deleted and an audit row is written to `forget_contact_audit`.

## 3. Retention

Every stored message is stamped `retention_until = now() + 18 months` at ingest.
A nightly job (`inbox.retention_sweep`, 03:00 UTC) hard-deletes expired rows.

## 4. Nightly predictions

`analysis.nightly` runs at 03:30 UTC and fully replaces the prediction tables:
`broker_predictions`, `lane_predictions`, `broker_lookalikes`, `objection_clusters`.
`/intelligence` reads them directly.

## 5. Env vars (Render / Vercel)

Required (production):
- `DATABASE_URL` — Postgres connection (Neon paid).
- `DATABASE_URL_DIRECT` — direct, no pgbouncer (alembic + worker use this).
- `JWT_SECRET` — session signing.
- `GEMINI_API_KEY` — paid-tier Gemini (startup assertion refuses to boot without it).
- `TENANT_CRED_KEY` — base64 32-byte key for `CredentialVault` AES-GCM.
- `CRON_SECRET` — guards `/jobs/drain` + mail cron routes.
- `NEXT_PUBLIC_API_URL` (Vercel) — the backend origin for the server-side typed client.

Optional:
- `GMAIL_SA_JSON` — fallback service-account JSON (prefer DB-stored).
- `MAIL_OWNER_SEND_ENABLED` — defaults false; the UI switch is the normal path.
- `SENTRY_DSN` — Sentry capture.
- `LOG_LEVEL` — defaults `INFO`.
- `OUTREACH_FROM_EMAIL` — the owner mailbox used as `From:` on sends.
