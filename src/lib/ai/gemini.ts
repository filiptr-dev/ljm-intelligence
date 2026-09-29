import type { AIProvider, BrokerSummary, EmailInsight, OutreachDraft } from "./types"

/**
 * Gemini provider (not active in the demo). Enable with
 *   AI_PROVIDER=gemini GEMINI_API_KEY=... [GEMINI_MODEL=gemini-2.5-flash]
 * Uses the REST generateContent endpoint with JSON output.
 */
const MODEL = process.env.GEMINI_MODEL ?? "gemini-2.5-flash"
const ENDPOINT = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`

async function generateJson<T>(prompt: string): Promise<T> {
  const res = await fetch(`${ENDPOINT}?key=${process.env.GEMINI_API_KEY}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [{ role: "user", parts: [{ text: prompt }] }],
      generationConfig: { responseMimeType: "application/json", temperature: 0.2 },
    }),
  })
  if (!res.ok) throw new Error(`Gemini ${res.status}: ${await res.text()}`)
  const data = await res.json()
  return JSON.parse(data.candidates[0].content.parts[0].text) as T
}

const CLASSIFY_PROMPT = `You analyse freight emails between a carrier (trucking company) and freight brokers.
For each email return JSON {emailId, intent, sentiment, confidence, rate, perUnit, distance, currency, lane:{origin,destination}, equipment, rejectionReason, evidence}.
intent ∈ load_offer|capacity_offer|quote|booked|rejected|declined|invoice|payment_issue|complaint|praise|other.
rejectionReason ∈ rate_too_high|already_covered|other_carrier|timing|equipment|compliance (only when intent=rejected).
sentiment is -1..1. Return a JSON array, one object per email, same order.`

export const geminiProvider: AIProvider = {
  name: "gemini",
  model: MODEL,
  async analyzeEmails(emails) {
    const out: EmailInsight[] = []
    for (let i = 0; i < emails.length; i += 40) {
      const batch = emails.slice(i, i + 40).map((e) => ({ emailId: e.id, direction: e.direction, subject: e.subject, body: e.body }))
      out.push(...(await generateJson<EmailInsight[]>(`${CLASSIFY_PROMPT}\n\n${JSON.stringify(batch)}`)))
    }
    return out
  },
  async summarizeBroker(input) {
    return generateJson<BrokerSummary>(
      `You are a freight sales analyst. Given these broker stats, return JSON {headline, summary, risks: string[], nextAction:{label, detail, campaign: "new"|"reengage"|"winback"|"none"}}. Be concrete and brief.\n\n${JSON.stringify(input)}`,
    )
  },
  async draftOutreach(input) {
    return generateJson<OutreachDraft>(
      `Write a short outreach email from a trucking carrier to a freight broker. Return JSON {subject, body}. Keep merge tags {{first_name}}, {{company}}, {{lane}}, {{equipment}}, {{sender}} literally. Campaign: ${input.campaign}. Tone: ${input.tone}. Equipment: ${input.equipment.join(", ")}. Lanes: ${input.lanes.join(", ")}.${input.brief ? ` The user describes the email they want as: """${input.brief}""". Follow it closely, and also return "understood": a short list of the points you took from it.` : ""}`,
    )
  },
}

