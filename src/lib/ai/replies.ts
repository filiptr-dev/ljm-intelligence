import type { Reply, ReplyCategory, Recipient } from "@/lib/campaigns/types"
import { pick, type Rng } from "@/lib/data/rng"

/**
 * Replies to outreach emails: realistic text for the demo, and the (mock) AI
 * that reads a reply and decides what it means. Client-safe, used live too.
 */

const TEXT: Record<ReplyCategory, string[]> = {
  interested: [
    "Hi {sender}, good timing. We are short on {equipment} capacity on {lane}. Can you send your carrier packet?",
    "Thanks for reaching out, we're interested. Please send your company profile and insurance certificate and we'll set you up.",
    "We'd like to add you to our carrier list. Can we set up a call this week?",
    "Yes, interested. We have regular {equipment} freight on {lane}, let's talk.",
  ],
  rates: [
    "What would your rate be for {lane}? We move about 4 loads a week on that lane.",
    "Can you quote {lane}, {equipment}, pickup Monday?",
    "How much would you charge per load on {lane}? Need a price for next week.",
  ],
  not_now: [
    "Thanks, we're covered for now but keep us posted for Q4.",
    "Not at the moment, please reach out again next month.",
    "We have enough capacity right now, maybe later in the season.",
  ],
  out_of_office: [
    "I am out of the office until Monday with limited access to email.",
    "Out of office: I'm on vacation and will reply when I'm back next week.",
  ],
  not_interested: [
    "We only work with our existing carriers, thanks.",
    "Not interested, we run our own fleet on those lanes.",
    "No thanks, not a fit for us.",
  ],
  unsubscribe: ["Please remove me from your mailing list.", "Unsubscribe.", "Stop emailing me please."],
}

/** Shippers phrase interest differently: they talk about their own freight. */
const SHIPPER_INTERESTED = [
  "We ship around 20 truckloads a month out of {city}. Can you send rates and your insurance certificate?",
  "Our current carrier keeps missing pickups. Can you call me this week about {lane}?",
  "Interested in working direct. What would a trial load on {lane} cost?",
]

export function makeReplyText(rng: Rng, category: ReplyCategory, r: Recipient, sender: string) {
  const pool = category === "interested" && r.companyType === "Shipper" ? SHIPPER_INTERESTED : TEXT[category]
  return pick(rng, pool)
    .replaceAll("{sender}", sender.split(" ")[0])
    .replaceAll("{lane}", r.lane ?? "your lanes")
    .replaceAll("{equipment}", r.equipment ?? "dry van")
    .replaceAll("{city}", r.lane?.split(" → ")[0] ?? "our plant")
}

const RULES: [ReplyCategory, RegExp][] = [
  ["unsubscribe", /remove me|unsubscribe|stop emailing/i],
  ["out_of_office", /out of (the )?office|on vacation|limited access/i],
  ["not_interested", /not interested|only work with|own fleet|no thanks|not a fit/i],
  ["not_now", /covered for now|not at the moment|maybe later|enough capacity|next month/i],
  ["rates", /\brate\b|\brates\b|quote|how much|price|cost/i],
  ["interested", /interested|carrier packet|carrier list|set up a call|let's talk|call me|insurance certificate|company profile/i],
]

const BASE_SENTIMENT: Record<ReplyCategory, number> = {
  interested: 0.8, rates: 0.55, not_now: 0.05, out_of_office: 0, not_interested: -0.5, unsubscribe: -0.8,
}

/** Mock of the Gemini reply classifier: reads the text and returns category + sentiment. */
export function analyzeReply(text: string, at: number, step: number): Reply {
  let category: ReplyCategory = "not_now"
  let hit = false
  for (const [cat, re] of RULES) {
    if (re.test(text)) {
      category = cat
      hit = true
      // "interested … send rates" is still interest with a price question
      if (cat === "rates" && /interested|call me|trial load/i.test(text)) category = "interested"
      break
    }
  }
  const lift = /good timing|keeps missing|let's talk|thanks/i.test(text) ? 0.1 : 0
  const sentiment = Math.max(-1, Math.min(1, BASE_SENTIMENT[category] + lift))
  return { text, category, sentiment: Math.round(sentiment * 100) / 100, confidence: hit ? 0.91 : 0.6, at, step }
}
