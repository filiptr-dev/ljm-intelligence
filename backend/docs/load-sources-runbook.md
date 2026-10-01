# Load sources runbook

Per-vendor onboarding, rotation, revocation. See
`projects/ljm-intelligence/plan/2026-10-01-load-board-connectors.md`.

**Standing rule — no scraping.** DAT, Truckstop, 123LB and CHR all forbid it.
Every adapter talks to the official REST API behind the vendor's signed
agreement; no browser automation.

## DAT One API

- Portal: https://developer.dat.com (requires executed agreement).
- Env on Render (`sync: false`):
  - `DAT_SERVICE_ACCOUNT_EMAIL`
  - `DAT_SERVICE_ACCOUNT_PASSWORD`
  - `DAT_ORG_ID`
- Rotation: regenerate service-account password in DAT dev-portal → Render
  env → redeploy → `/loads/sources/dat/test` must return `ok=true`.
- Revocation: clear the three env vars → redeploy → in DAT portal, disable the
  service account.

## C.H. Robinson Navisphere Carrier

- Portal: contact your CHR carrier rep to request Navisphere Carrier API
  access.
- Env: `CHR_CLIENT_ID`, `CHR_CLIENT_SECRET`, `CHR_CARRIER_CODE`.
- Rotation: regenerate the OAuth client-secret in the CHR portal → Render →
  redeploy.
- Revocation: clear env → ask CHR to revoke the client credential.

## 123Loadboard

- Portal: https://www.123loadboard.com (API agreement required).
- Env: `LB123_API_KEY`, `LB123_CARRIER_USERNAME`, `LB123_CARRIER_PASSWORD`.
- Rotation: new API key issued by 123LB account manager → Render.
- Revocation: clear env → ask 123LB to revoke the API key.

## Truckstop Load Board Pro

- Portal: Truckstop account rep (SIA required).
- Env: `TRUCKSTOP_INTEGRATION_ID`, `TRUCKSTOP_USERNAME`, `TRUCKSTOP_PASSWORD`.
- Rotation: new integration credential issued by Truckstop → Render.
- Revocation: clear env → Truckstop rep rotates the credential.

## Secret-handling rules (apply to all vendors)

- Credentials live only in Render dashboard env (encrypted at rest).
- Never commit a value to git; `.env.example` carries key names only.
- Never place a secret into a browser-reachable form — the Settings UI does
  not collect any; "Test connection" uses what Render already stores.
