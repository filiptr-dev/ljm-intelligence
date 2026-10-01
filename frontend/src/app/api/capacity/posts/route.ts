/** Capacity posts proxy → FastAPI `/capacity/posts`. */

import { authHeaders } from "@/lib/auth/bff"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const url = new URL(req.url)
  const qs = url.search
  try {
    const r = await fetch(`${BACKEND}/capacity/posts${qs}`, { headers: authHeaders(req), cache: "no-store" })
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } })
  } catch (e) {
    return Response.json({ items: [], error: String(e) }, { status: 502 })
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.text()
    const r = await fetch(`${BACKEND}/capacity/posts`, {
      method: "POST",
      headers: authHeaders(req, { "content-type": "application/json" }),
      body,
      cache: "no-store",
    })
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}
