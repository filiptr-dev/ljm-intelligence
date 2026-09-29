import { backend } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET() {
  try {
    const latest = await backend.latestRun()
    return Response.json(latest)
  } catch {
    return Response.json(null, { status: 502 })
  }
}
