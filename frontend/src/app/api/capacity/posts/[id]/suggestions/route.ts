/** Suggestions proxy → FastAPI `/capacity/posts/{id}/suggestions`. */

import { authHeaders } from "@/lib/auth/bff"

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const url = new URL(req.url)
  const qs = url.search
  try {
    const r = await fetch(`${BACKEND}/capacity/posts/${encodeURIComponent(id)}/suggestions${qs}`, { headers: authHeaders(req), cache: "no-store" })
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } })
  } catch (e) {
    return Response.json({ post: null, items: [], error: String(e) }, { status: 502 })
  }
}
