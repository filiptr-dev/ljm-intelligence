/** Outcome logger proxy → FastAPI `POST /tools/call-list/outcome`.
 *
 * Body is passed through verbatim; the backend validates. On failure we return
 * a 502 so the client can toast and NOT clobber its optimistic list state.
 */

const BACKEND = process.env.BACKEND_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  try {
    const body = await req.text()
    const r = await fetch(`${BACKEND}/tools/call-list/outcome`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
    })
    return new Response(await r.text(), {
      status: r.status,
      headers: { "content-type": "application/json" },
    })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}
