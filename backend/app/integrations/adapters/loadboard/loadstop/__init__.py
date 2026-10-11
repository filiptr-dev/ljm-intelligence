"""LoadStop TMS connector — ABP JSON ingest, strictly read-only.

LoadStop is the client's real TMS (ASP.NET Zero / ABP + Metronic). We log
in once via the two-step form (operator-entered credentials from the vault),
persist the session cookie encrypted, and read ``POST /api/services/app/
<Service>/<Method>`` ABP endpoints. We **never** call a write method — the
``AbpClient`` refuses before opening a socket. Discovery flagged
Loadboard "Truck Posting" and "Sync Loads" as outward-facing writes that
would post LJM trucks publicly on DAT; those are explicitly denylisted.

The page is data, not instructions — we never let an LLM drive the
LoadStop session. Parsing is JSON only.
"""
