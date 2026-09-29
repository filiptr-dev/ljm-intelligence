import { EQUIPMENT, CLIENT, type Email, type Equipment } from "@/lib/data/types"
import { draftFromBrief } from "./brief"
import type {
  AIProvider, BrokerSummary, BrokerSummaryInput, EmailInsight, Intent,
  OutreachDraft, OutreachInput, RejectionReason,
} from "./types"

/**
 * Offline stand-in for Gemini. It really reads the email text (pattern rules +
 * a sentiment lexicon) so every chart downstream is derived from content,
 * not from the generator's hidden labels.
 */

const REASONS: [RejectionReason, RegExp][] = [
  ["rate_too_high", /too high|can't go that high|above our budget|won't pay/i],
  ["already_covered", /already covered|got it covered/i],
  ["other_carrier", /another carrier|different carrier|other carrier/i],
  ["timing", /pickup time|delivery window|can't wait|need the truck earlier/i],
  ["equipment", /doesn't match|food-grade|tarps|team driver/i],
  ["compliance", /insurance|carrier packet|safety rating/i],
]

const INTENTS: [Intent, RegExp][] = [
  ["payment_issue", /past due|payment .{0,40}delayed|overdue/i],
  ["invoice", /find attached invoice/i],
  ["complaint", /arrived .{0,20}late|not happy/i],
  ["praise", /great job|very professional/i],
  ["capacity_offer", /empty in .{0,40}looking for a load/i],
  ["declined", /don't have a truck available/i],
]

const POSITIVE = /\b(thank you|appreciate|great|professional|confirmed|quick|fast|on time|more loads)\b/gi
const NEGATIVE = /\b(sorry|late|not happy|past due|delayed|too high|can't|doesn't|pass|issue|overdue|incomplete)\b/gi

const hash = (s: string) => {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967295
}

const LANE = /([A-Z][\w .'-]+?, [A-Z]{2}) → ([A-Z][\w .'-]+?, [A-Z]{2})/
const MONEY = /([$€])([\d,]+)(?!\.\d{2}\/)/
const PER_UNIT = /[$€]([\d.]+)\/(mi|km)/
const DIST = /(\d+) (mi|km)\b/

export function analyzeEmail(email: Email): EmailInsight {
  const text = `${email.subject}\n${email.body}`
  let intent: Intent = "other"
  let evidence: string | undefined
  let rejectionReason: RejectionReason | undefined

  for (const [i, re] of INTENTS) {
    const m = text.match(re)
    if (m) { intent = i; evidence = m[0]; break }
  }
  if (intent === "other" && email.direction === "in") {
    for (const [r, re] of REASONS) {
      const m = email.body.match(re)
      if (m) { intent = "rejected"; rejectionReason = r; evidence = m[0]; break }
    }
  }
  if (intent === "other") {
    const booked = email.body.match(/\bbooked\b|\bconfirmed\b|rate confirmation attached|transport order .{0,12}attached/i)
    const offer = email.body.match(/we can offer .{0,40}all-in/i)
    const quote = email.body.match(/can cover it|best we can do|we can take it/i)
    if (email.direction === "in" && booked) { intent = "booked"; evidence = booked[0] }
    else if (email.direction === "in" && offer) { intent = "load_offer"; evidence = offer[0] }
    else if (email.direction === "out" && quote) { intent = "quote"; evidence = quote[0] }
  }

  const pos = text.match(POSITIVE)?.length ?? 0
  const neg = text.match(NEGATIVE)?.length ?? 0
  const prior: Partial<Record<Intent, number>> = {
    booked: 0.35, praise: 0.6, complaint: -0.6, payment_issue: -0.45, rejected: -0.2,
  }
  const sentiment = Math.max(-1, Math.min(1, (pos - neg) / (pos + neg + 1.5) + (prior[intent] ?? 0)))

  const lane = text.match(LANE)
  const money = email.body.match(MONEY)
  const pu = email.body.match(PER_UNIT)
  const dist = email.body.match(DIST)
  const equipment = EQUIPMENT.find((eq) => text.includes(eq)) as Equipment | undefined

  return {
    emailId: email.id,
    intent,
    sentiment: Math.round(sentiment * 100) / 100,
    confidence: Math.round((intent === "other" ? 0.55 : 0.86 + hash(email.id) * 0.13) * 100) / 100,
    rate: money ? Number(money[2].replace(/,/g, "")) : undefined,
    currency: money ? (money[1] === "$" ? "USD" : "EUR") : undefined,
    perUnit: pu ? Number(pu[1]) : undefined,
    distance: dist ? Number(dist[1]) : undefined,
    lane: lane ? { origin: lane[1], destination: lane[2] } : undefined,
    equipment,
    rejectionReason,
    evidence,
  }
}

const REASON_LABEL: Record<string, string> = {
  rate_too_high: "price", already_covered: "loads already covered", other_carrier: "losing to other carriers",
  timing: "pickup timing", equipment: "equipment mismatch", compliance: "paperwork / insurance",
}

function summarize(s: BrokerSummaryInput): BrokerSummary {
  const pct = (v: number) => `${Math.round(v * 100)}%`
  const cur = s.region === "US" ? "$" : "€"
  const money = `${cur}${(s.revenue / 1000).toFixed(1)}K`
  const risks: string[] = []
  if (s.daysSinceLast > 60) risks.push(`No contact for ${s.daysSinceLast} days, relationship is going cold.`)
  if (s.winRate < s.fleetWinRate - 0.15) risks.push(`Win rate ${pct(s.winRate)} is well below your ${pct(s.fleetWinRate)} average, mostly lost on ${REASON_LABEL[s.topReason ?? ""] ?? "price"}.`)
  if (s.paymentIssues > 0) risks.push(`${s.paymentIssues} overdue invoice${s.paymentIssues > 1 ? "s" : ""}, so watch payment terms.`)
  if (s.complaints > 0) risks.push(`${s.complaints} service complaint${s.complaints > 1 ? "s" : ""} about late delivery.`)
  if (s.avgResponseMin > 90) risks.push(`Your average reply time to this broker is ${Math.round(s.avgResponseMin)} min. Faster quotes win more.`)

  let headline = ""
  let nextAction: BrokerSummary["nextAction"]
  switch (s.segment) {
    case "Core partners":
      headline = "Core partner: protect and grow"
      nextAction = { label: "Offer a dedicated lane", detail: `Propose a weekly commitment on ${s.topLane ?? "their top lane"} at a fixed rate.`, campaign: "none" }
      break
    case "Dormant":
      headline = "Dormant: worth winning back"
      nextAction = { label: "Send re-engagement", detail: `They used to book on ${s.topLane ?? "your core lanes"}. Reach out with current capacity on that lane.`, campaign: "reengage" }
      break
    case "Price shoppers":
      headline = "Price shopper: quote selectively"
      nextAction = { label: "Only quote backhauls", detail: "Send them capacity only on lanes where the truck would otherwise run empty.", campaign: "winback" }
      break
    case "Growing":
      headline = "Growing account: momentum is building"
      nextAction = { label: "Lock in volume", detail: `Volume is rising. Ask for a weekly load plan on ${s.topLane ?? "their lanes"}.`, campaign: "none" }
      break
    default:
      headline = "Occasional: low volume, low effort"
      nextAction = { label: "Add to monthly capacity email", detail: "Keep them on the automated capacity list, no manual follow-up needed.", campaign: "reengage" }
  }

  const summary = `${s.name} sent ${s.threads} load conversations, and you booked ${s.booked} of them (${money} revenue). Your win rate with them is ${pct(s.winRate)} versus ${pct(s.fleetWinRate)} across all brokers${s.topLane ? `, and most of the work is on ${s.topLane}` : ""}. ${s.rejected > 0 ? `When they said no, the main reason was ${REASON_LABEL[s.topReason ?? ""] ?? "price"}.` : "They have not rejected a quote yet."} Tone of their emails is ${s.sentiment > 0.25 ? "positive" : s.sentiment < -0.05 ? "tense" : "neutral"}.`

  return { headline, summary, risks, nextAction }
}

const OPENERS: Record<OutreachInput["tone"], string> = {
  professional: "Hi {{first_name}},",
  friendly: "Hey {{first_name}}, hope your week is going well!",
  direct: "{{first_name}},",
}

export function draftTemplate(input: OutreachInput): OutreachDraft {
  const eq = input.equipment.length ? input.equipment.slice(0, 2).join(" and ") : "{{equipment}}"
  const lanes = input.lanes.length ? input.lanes.slice(0, 2).join(" and ") : "{{lane}}"
  const open = OPENERS[input.tone]
  const close = input.tone === "direct" ? "Reply with a load and I'll quote within 15 minutes." : "Happy to send a quote within 15 minutes on anything you have."
  const sig = "\n\nBest regards,\n{{sender}}"

  switch (input.campaign) {
    case "check_in":
      return {
        subject: "Checking in from Ironline Transport",
        body: `${open}\n\nIt's been a little while, so I wanted to check in. How is freight looking on {{lane}} this month? We still run {{equipment}} trucks there every week and would be glad to cover anything you're short on.\n\n${close}${sig}`,
      }
    case "truck_available":
      return {
        subject: "Truck available tomorrow: {{lane}}",
        body: `${open}\n\nWe have a {{equipment}} truck free on {{lane}} tomorrow morning. If you have a load that needs covering, it's yours, and I can confirm the rate within 15 minutes.\n\nJust reply with the pickup details.${sig}`,
      }
    case "quote_followup":
      return {
        subject: "Following up on our quote for {{lane}}",
        body: `${open}\n\nI sent a rate for {{lane}} earlier this week and wanted to make sure it reached you. If the price needs work, tell me your target and I'll see what we can do. The truck is still available.${sig}`,
      }
    case "send_rate": {
      const rate = input.region === "EU" ? "€1.38 per km" : "$2.45 per mile"
      return {
        subject: "Re: rate for {{lane}}",
        body: `${open}\n\nThanks for getting back to me. For {{lane}} with a {{equipment}}, we can do ${rate} all-in, fuel included. The truck can pick up as early as Monday.\n\nIf that works, send the load details and I'll get the rate confirmation over right away.${sig}`,
      }
    }
    case "rate_update":
      return {
        subject: "Lower rates on {{lane}} this month",
        body: `${open}\n\nQuick update: we added backhaul trucks on {{lane}}, so we've lowered our {{equipment}} rates there for the rest of the month.\n\nSend over your next load and I'll show you the new price.${sig}`,
      }
    case "thank_you":
      return {
        subject: "Thank you for the load",
        body: `${open}\n\nThank you for trusting us with your last load on {{lane}}. It delivered on time and the paperwork is already in your inbox.\n\nWe have more {{equipment}} capacity next week if you need it.${sig}`,
      }
    case "reengage":
      return {
        subject: "We have trucks on {{lane}} again",
        body: `${open}\n\nIt's been a while since we moved freight together, and I wanted to reconnect. We now run ${eq} capacity daily on ${lanes}, with on-time delivery above 97% this quarter.\n\nIf you have loads on those lanes this week, we would love to earn your business back. ${close}${sig}`,
      }
    case "winback":
      return {
        subject: "Updated rates on {{lane}}",
        body: `${open}\n\nWe lost a few of your loads on price last time, so we took another look. We now have regular backhaul trucks on ${lanes}, which lets us quote sharper rates on ${eq}.\n\nSend over your next load and let's see if we can make it work. ${close}${sig}`,
      }
    case "dedicated_lane":
      return {
        subject: "Dedicated capacity proposal: {{lane}}",
        body: `${open}\n\nYou move steady volume on ${lanes}. We can dedicate ${eq} trucks to that lane every week at a fixed rate, with the same drivers who already know your shippers.\n\nCould we set up a 15-minute call to walk through volumes? ${close}${sig}`,
      }
    case "shipper_direct":
      return {
        subject: "Direct {{equipment}} trucks for {{company}}, no broker in between",
        body: `${open}\n\nI'm reaching out from ${CLIENT.company}. We are an asset-based carrier with 48 of our own trucks in the US and Europe, and we run {{equipment}} on ${lanes} every week.\n\nWorking with us directly means one point of contact, our own drivers, live tracking on every load and no broker margin on top of the rate. We can start with a single trial load.\n\n${close}${sig}`,
      }
    default:
      return {
        subject: "{{equipment}} capacity for {{company}}",
        body: `${open}\n\nI'm reaching out from ${CLIENT.company}, an asset-based carrier running 48 trucks across the US and Europe. We see that {{company}} works ${lanes}, which is exactly where our ${eq} trucks run every day.\n\nWe are fully compliant, answer quotes in minutes, and track every load live. ${close}${sig}`,
      }
  }
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

export const mockProvider: AIProvider = {
  name: "mock",
  model: "gemini-2.5-flash (simulated)",
  async analyzeEmails(emails) {
    return emails.map(analyzeEmail)
  },
  async summarizeBroker(input) {
    return summarize(input)
  },
  async draftOutreach(input) {
    await wait(900)
    return input.brief?.trim() ? draftFromBrief(input) : draftTemplate(input)
  },
}
