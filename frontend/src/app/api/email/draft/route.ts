import { backend, type EmailTone } from "@/lib/backend"

export const dynamic = "force-dynamic"

type Body = {
  lead_id?: string
  lead?: Record<string, unknown>
  tone?: EmailTone
  instructions?: string
}

export async function POST(req: Request) {
  const body = (await req.json()) as Body
  try {
    const draft = await backend.draftEmail(undefined, {
      lead_id: body.lead_id,
      lead: body.lead,
      tone: body.tone ?? "professional",
      instructions: body.instructions,
    })
    return Response.json(draft)
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 })
  }
}
