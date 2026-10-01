import { backend } from "@/lib/backend"
import { readSessionCookie } from "@/lib/auth/bff"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const token = readSessionCookie(req) ?? undefined
  try {
    const latest = await backend.latestRun(undefined, token)
    return Response.json(latest)
  } catch {
    return Response.json(null, { status: 502 })
  }
}
