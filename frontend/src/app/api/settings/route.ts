/**
 * Owner settings proxy → FastAPI `/settings`.
 * Kept server-side so we don't have to broaden CORS on the backend.
 */

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET() {
  try {
    const r = await fetch(`${BACKEND}/settings`, { cache: "no-store" })
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
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
    })
    return new Response(await r.text(), { status: r.status, headers: { "content-type": "application/json" } })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}
