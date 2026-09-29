import { getAI, type OutreachInput } from "@/lib/ai"

export async function POST(req: Request) {
  const input = (await req.json()) as OutreachInput
  const draft = await getAI().draftOutreach(input)
  return Response.json(draft)
}
