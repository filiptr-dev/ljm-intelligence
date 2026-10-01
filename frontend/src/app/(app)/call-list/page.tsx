/**
 * Call List — /call-list. SERVER COMPONENT shell.
 *
 * The operator's daily phone queue. The ranked list is produced by the
 * deterministic `call_rank` scorer (backend/app/pipeline/call_rank.py);
 * this shell asks the backend for it once, server-side, so the first paint
 * carries real rows and the mount-time flash + spinner go away.
 *
 * Everything interactive (selection, history fetch per lead, outcome
 * logging, callback date picker, backend-health badge) lives in the client
 * island (`./call-list-client.tsx`). The island talks to the backend via
 * the typed openapi-fetch client over `/api/proxy` — the HttpOnly session
 * cookie is the authority; no bearer in JS.
 */

import { api } from "@/lib/api/server"
import CallListClient, { type CallListEnvelope } from "./call-list-client"

export const dynamic = "force-dynamic"

export default async function CallListPage() {
  let initial: CallListEnvelope | null = null
  try {
    const { data, response } = await api.GET("/tools/call-list", { params: { query: { limit: 25 } } })
    if (response.ok && data) initial = data as unknown as CallListEnvelope
  } catch {
    initial = null
  }
  return <CallListClient initial={initial} />
}
