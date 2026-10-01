/** Call list proxy → FastAPI `/tools/call-list`.
 *
 * Mirrors the /api/capacity/posts proxy shape: opaque pass-through so the browser
 * never sees BACKEND_URL and no CORS is needed. On backend failure we return an
 * empty {date, items} envelope with a 502 status — the client uses the status
 * (not the body) to flip its "backend asleep" badge honestly.
 */

import { authHeaders } from "@/lib/auth/bff"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const url = new URL(req.url)
  const qs = url.search
  try {
    const r = await fetch(`${BACKEND}/tools/call-list${qs}`, { headers: authHeaders(req), cache: "no-store" })
    return new Response(await r.text(), {
      status: r.status,
      headers: { "content-type": "application/json" },
    })
  } catch (e) {
    return Response.json(
      { date: new Date().toISOString().slice(0, 10), items: [], error: String(e) },
      { status: 502 },
    )
  }
}
