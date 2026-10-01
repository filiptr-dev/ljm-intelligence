/**
 * Owner settings proxy → FastAPI `/settings`.
 * Kept server-side so we don't have to broaden CORS on the backend.
 *
 * Auth: forwards the HttpOnly `ljm_session` cookie as `Authorization: Bearer`
 * via the shared `authHeaders` helper (see `@/lib/auth/bff`). Without this
 * the backend returns 401 and the UI surfaces "Couldn't load settings — 401".
 */

import { authHeaders } from "@/lib/auth/bff"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  try {
    const r = await fetch(`${BACKEND}/settings`, {
      headers: authHeaders(req),
      cache: "no-store",
    })
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}

export async function PUT(req: Request) {
  try {
    const body = await req.text()
    const r = await fetch(`${BACKEND}/settings`, {
      method: "PUT",
      headers: authHeaders(req, { "content-type": "application/json" }),
      body,
      cache: "no-store",
    })
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}
