// DEPRECATED. The rich email builder on /emails/compose + the thread reply
// now talk to the server-side `/inbox/ai-draft` + `/inbox/rewrite`
// endpoints. This route is kept only for `/outreach` (the campaign
// builder) until the campaign-send follow-up moves it over.
import { getAI, type OutreachInput } from "@/lib/ai"

export async function POST(req: Request) {
  const input = (await req.json()) as OutreachInput
  const draft = await getAI().draftOutreach(input)
  return Response.json(draft)
}
