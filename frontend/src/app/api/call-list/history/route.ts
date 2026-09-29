/** Outcome history proxy → FastAPI `GET /tools/call-list/history?lead_id=`. */

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const url = new URL(req.url)
  const qs = url.search
  try {
    const r = await fetch(`${BACKEND}/tools/call-list/history${qs}`, { cache: "no-store" })
    return new Response(await r.text(), {
      status: r.status,
      headers: { "content-type": "application/json" },
    })
  } catch (e) {
    return Response.json(
      { lead_id: "", items: [], error: String(e) },
      { status: 502 },
    )
  }
}
