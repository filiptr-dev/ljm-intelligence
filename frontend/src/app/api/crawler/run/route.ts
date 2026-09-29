import { backend } from "@/lib/backend"

export const dynamic = "force-dynamic"

// Server-side only. Holds CRON_SECRET; never exposes it to the browser.
export async function POST(req: Request) {
  const url = new URL(req.url)
  const limit = Number(url.searchParams.get("limit") || 25)
  try {
    const res = await backend.triggerRun(undefined, Math.max(1, Math.min(limit, 200)))
    return Response.json(res, { status: 202 })
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}
