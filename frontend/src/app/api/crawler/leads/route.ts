import { backend } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const url = new URL(req.url)
  try {
    const page = await backend.listLeads(undefined, {
      limit: Number(url.searchParams.get("limit") || 100),
      state: url.searchParams.get("state") || undefined,
      kind: url.searchParams.get("kind") || undefined,
      min_score: url.searchParams.get("min_score") ? Number(url.searchParams.get("min_score")) : undefined,
    })
    return Response.json(page)
  } catch (e) {
    return Response.json({ items: [], total: 0, limit: 0, offset: 0, error: String(e) }, { status: 502 })
  }
}
