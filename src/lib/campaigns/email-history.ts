import { draftTemplate } from "@/lib/ai/mock"
import { analyzeReply, makeReplyText } from "@/lib/ai/replies"
import type { EmailPurpose, OutreachTone } from "@/lib/ai/types"
import type { BrokerStats, Segment } from "@/lib/analytics"
import { chance, createRng, int, pick, weighted } from "@/lib/data/rng"
import { CLIENT, DEMO_NOW, type Broker, type Lead } from "@/lib/data/types"
import { PURPOSE_LABEL, type Campaign, type CampaignRecipient, type Recipient, type ReplyCategory } from "./types"

/**
 * The last month of one-off emails: quick personal notes the dispatcher sent to
 * single companies outside of campaigns. Personal emails get more replies than
 * campaign emails, and what they ask for depends on why the email was sent.
 */

const DAY = 86_400_000
const HOUR = 3_600_000

const PURPOSE_BY_SEGMENT: Record<Segment, EmailPurpose[]> = {
  "Core partners": ["truck_available", "thank_you", "truck_available"],
  Growing: ["truck_available", "quote_followup", "thank_you"],
  "Price shoppers": ["rate_update", "quote_followup", "send_rate"],
  Dormant: ["check_in", "check_in", "rate_update"],
  Occasional: ["check_in", "truck_available"],
}

const REPLY_BY_PURPOSE: Record<EmailPurpose, [ReplyCategory, number][]> = {
  intro: [["interested", 3], ["rates", 2], ["not_now", 3], ["not_interested", 2], ["out_of_office", 1]],
  check_in: [["interested", 3], ["rates", 2], ["not_now", 4], ["out_of_office", 1], ["not_interested", 1]],
  truck_available: [["rates", 5], ["interested", 3], ["not_now", 2]],
  quote_followup: [["rates", 4], ["interested", 2], ["not_now", 2], ["not_interested", 2]],
  send_rate: [["interested", 4], ["rates", 2], ["not_interested", 2]],
  rate_update: [["rates", 4], ["interested", 2], ["not_now", 3], ["not_interested", 1]],
  thank_you: [["interested", 6], ["rates", 2], ["not_now", 1]],
}

const PLAIN = { layout: "plain", accent: "#f5b800", showLogo: false, showTruck: false, ctaLabel: "", ctaUrl: "", signature: true } as const

export function buildEmailHistory(brokers: Broker[], stats: Map<string, BrokerStats>, leads: Lead[]): Campaign[] {
  const rng = createRng(31337)
  const out: Campaign[] = []
  const now = DEMO_NOW.getTime()
  const pool = [...brokers].sort(() => rng() - 0.5)
  const leadPool = leads.filter((l) => l.emailVerified && l.score >= 60)

  for (let i = 0; i < 34; i++) {
    const toLead = i % 5 === 4
    let recipient: Recipient
    let purpose: EmailPurpose
    if (toLead) {
      const l = leadPool[(i * 7) % leadPool.length]
      recipient = {
        id: l.id, kind: "lead", companyType: l.kind, name: l.name, contactName: l.contact.name, email: l.contact.email,
        region: l.region, lane: `${l.lanes[0].origin} → ${l.lanes[0].destination}`, equipment: l.equipment[0], verified: true,
      }
      purpose = "intro"
    } else {
      const b = pool[i % pool.length]
      const st = stats.get(b.id)!
      recipient = {
        id: b.id, kind: "broker", name: b.name, contactName: b.contact.name, email: b.contact.email, region: b.region,
        lane: st.topLane ?? `${b.lanes[0].origin} → ${b.lanes[0].destination}`, equipment: b.equipment[0], verified: true,
      }
      purpose = pick(rng, PURPOSE_BY_SEGMENT[st.segment])
    }

    // spread over the last 30 days, busier recently; office hours only
    const d = new Date(now - Math.pow(i / 34, 1.3) * 30 * DAY - int(rng, 1, 5) * HOUR)
    if (d.getUTCDay() === 0) d.setUTCDate(d.getUTCDate() - 2)
    if (d.getUTCDay() === 6) d.setUTCDate(d.getUTCDate() - 1)
    d.setUTCHours(int(rng, 7, 16), int(rng, 0, 59), 0, 0)
    const sentAt = Math.min(d.getTime(), now - 2 * HOUR)
    const tone: OutreachTone = pick(rng, ["professional", "friendly", "friendly", "direct"])
    const draft = draftTemplate({ campaign: purpose, tone, region: recipient.region, equipment: [], lanes: [] })

    const age = now - sentAt
    const base: CampaignRecipient = { ...recipient, status: "sent", at: sentAt, step: 1 }
    let r: CampaignRecipient = base
    if (chance(rng, age < 6 * HOUR ? 0.5 : 0.8)) {
      r = { ...base, status: "opened", at: sentAt + int(rng, 5, 180) * 60_000 }
      const replyChance = age < 3 * HOUR ? 0.15 : toLead ? 0.4 : 0.72
      if (chance(rng, replyChance)) {
        // most personal replies come within a few hours, a few the next day
        const at = Math.min(now - 30 * 60_000, sentAt + (12 + Math.pow(rng(), 2.2) * 60 * 26) * 60_000)
        const category = weighted(rng, REPLY_BY_PURPOSE[purpose])
        const reply = analyzeReply(makeReplyText(rng, category, recipient, CLIENT.dispatcher), at, 1)
        const won = (category === "interested" || category === "rates") && chance(rng, 0.45) && age > 2 * DAY
        r = { ...base, status: "replied", at, reply, won, loads: won ? int(rng, 1, 4) : 0 }
      }
    }

    out.push({
      id: `m${i + 1}`,
      name: `${PURPOSE_LABEL[purpose]} · ${recipient.name}`,
      type: purpose,
      tone,
      auto: false,
      single: true,
      historical: true,
      createdAt: sentAt,
      subject: draft.subject,
      body: draft.body,
      design: PLAIN,
      schedule: { mode: "now", at: sentAt },
      followUps: purpose === "quote_followup" || purpose === "intro" ? [{ afterDays: 3, subject: `Re: ${draft.subject}`, body: "" }] : [],
      followUpsSent: 0,
      recipients: [r],
    })
  }
  return out.sort((a, b) => b.createdAt - a.createdAt)
}
