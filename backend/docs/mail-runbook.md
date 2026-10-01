# Mail connector runbook

Access-day switch-on, rotation, and revocation for the Google Workspace mail
connector. See `projects/ljm-intelligence/plan/2026-10-01-google-workspace-mail-connector.md`.

## Switch on

1. Super-admin runs Domain-Wide Delegation for our service-account **Client
   ID**. Grant exactly these scopes:
   `https://www.googleapis.com/auth/gmail.readonly, https://www.googleapis.com/auth/gmail.send, https://www.googleapis.com/auth/admin.directory.user.readonly`.
2. In Render dashboard, set:
   - `GMAIL_SA_JSON` — the full service-account JSON.
   - `GMAIL_IMPERSONATE` — e.g. `contact@ljminternational.com`.
   - `GMAIL_ADMIN_IMPERSONATE` — a super-admin whose user we impersonate for
     the Admin SDK call.
   - `OUTREACH_POSTAL_ADDRESS` — real LJM postal address (CAN-SPAM).
   - `MAIL_SENDER=gmail`, `MAILBOX_SOURCE=gmail` (or flip in `/settings`).
3. In `/settings` → Mail connection, click **Test connection**. The SA
   fingerprint, scopes, and a sample `message_id` appear on success.

## Rotation (quarterly)

1. Google Cloud → IAM → Service Accounts → `<sa>` → Keys → Add Key → JSON.
2. Replace `GMAIL_SA_JSON` on Render; redeploy.
3. `/mail/status` must show the new SA fingerprint; `/mail/test-send` must succeed.
4. Delete the old key in Google Cloud.
5. Log the rotation (date, operator) in `mail-rotation-log.md`.

## Revocation (compromise)

1. `/settings` → Mail connection → **Disconnect** (DB override to simulated,
   immediate — no redeploy needed).
2. Delete `GMAIL_SA_JSON` on Render; redeploy.
3. Google Cloud → disable then delete the SA key.
4. Google Admin → Security → Domain-Wide Delegation → remove the client ID.
5. Optional: `DELETE FROM mail_messages WHERE mailbox = :m` and bump
   `mail_cursors.history_id` past the current value so re-ingest cannot
   resurrect the data.
