import { backend } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET() {
  try {
    const h = await backend.health()
    return Response.json({ ...h, backend: backend.base })
  } catch {
    return Response.json({ ok: false, db: "down", backend: backend.base }, { status: 503 })
  }
}
