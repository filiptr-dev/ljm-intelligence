import { draftTemplate } from "@/lib/ai/mock"
import { analyzeReply, makeReplyText } from "@/lib/ai/replies"
import type { CampaignType, OutreachTone } from "@/lib/ai/types"
import type { BrokerStats, Segment } from "@/lib/analytics"
import { makeLead } from "@/lib/data/leads"
import { chance, createRng, int, weighted, type Rng } from "@/lib/data/rng"
import { CLIENT, DEMO_NOW, type Broker, type LeadKind } from "@/lib/data/types"
import type { Campaign, CampaignRecipient, FollowUp, Goal, Recipient, ReplyCategory } from "./types"

/**
 * Six months of past campaigns so the Campaigns page has a history to analyse.
 * Response rates differ by campaign type, tone, send day and follow-ups the way
 * they do in freight sales, so the analysis surfaces real patterns.
 */

const DAY = 86_400_000
const HOUR = 3_600_000

type Def = {
  id: string
  daysAgo: number
  weekday: number // 1 = Monday
  hour: number
  name: string
  type: CampaignType
  tone: OutreachTone
  subject: string
  goal: Goal
  audience: { existing?: Segment; kinds?: LeadKind[]; industry?: string[]; region?: "US" | "EU"; equipment?: string }
  size: number
  open: number
  reply: number
  positive: number
  win: number
  followUps: number
}

const DEFS: Def[] = [
  { id: "h1", daysAgo: 172, weekday: 2, hour: 9, name: "Capacity intro · Midwest & Texas brokers", type: "new_leads", tone: "professional", subject: "Dry van & reefer capacity on Midwest–Texas lanes", goal: { type: "accounts", target: 6 }, audience: { kinds: ["Broker"], region: "US" }, size: 60, open: 0.52, reply: 0.1, positive: 0.52, win: 0.35, followUps: 1 },
  { id: "h2", daysAgo: 151, weekday: 5, hour: 16, name: "Reefer summer push", type: "new_leads", tone: "friendly", subject: "Summer reefer capacity, book before the peak", goal: { type: "loads", target: 20 }, audience: { kinds: ["Broker", "Forwarder"], equipment: "Reefer" }, size: 45, open: 0.36, reply: 0.06, positive: 0.45, win: 0.3, followUps: 0 },
  { id: "h3", daysAgo: 128, weekday: 3, hour: 8, name: "Win-back · dormant brokers", type: "reengage", tone: "professional", subject: "We have trucks on your lanes again", goal: { type: "replies", target: 8 }, audience: { existing: "Dormant" }, size: 40, open: 0.62, reply: 0.2, positive: 0.58, win: 0.4, followUps: 1 },
  { id: "h4", daysAgo: 109, weekday: 4, hour: 10, name: "Backhaul offer · price shoppers", type: "winback", tone: "direct", subject: "Backhaul rates on your lanes this month", goal: { type: "loads", target: 15 }, audience: { existing: "Price shoppers" }, size: 35, open: 0.55, reply: 0.15, positive: 0.42, win: 0.35, followUps: 0 },
  { id: "h5", daysAgo: 96, weekday: 2, hour: 8, name: "EU tautliner intro · DACH & Balkans", type: "new_leads", tone: "professional", subject: "Tautliner capacity Germany ⇄ Balkans every week", goal: { type: "accounts", target: 5 }, audience: { kinds: ["Broker", "Forwarder"], region: "EU" }, size: 50, open: 0.5, reply: 0.12, positive: 0.5, win: 0.33, followUps: 1 },
  { id: "h6", daysAgo: 76, weekday: 3, hour: 9, name: "Direct shippers · food & beverage", type: "shipper_direct", tone: "friendly", subject: "Your own reefer trucks, without the broker margin", goal: { type: "accounts", target: 5 }, audience: { kinds: ["Shipper"], industry: ["Food & beverage", "Agriculture"] }, size: 40, open: 0.6, reply: 0.18, positive: 0.68, win: 0.42, followUps: 2 },
  { id: "h7", daysAgo: 55, weekday: 2, hour: 10, name: "Dedicated lane offer · core partners", type: "dedicated_lane", tone: "professional", subject: "Weekly dedicated trucks on your main lane", goal: { type: "loads", target: 20 }, audience: { existing: "Core partners" }, size: 16, open: 0.88, reply: 0.55, positive: 0.9, win: 0.75, followUps: 0 },
  { id: "h8", daysAgo: 34, weekday: 4, hour: 9, name: "Direct shippers · building materials & steel", type: "shipper_direct", tone: "direct", subject: "Flatbed & step deck trucks, direct from the carrier", goal: { type: "accounts", target: 6 }, audience: { kinds: ["Shipper"], industry: ["Building materials", "Steel & metals"] }, size: 45, open: 0.55, reply: 0.14, positive: 0.62, win: 0.38, followUps: 1 },
  { id: "h9", daysAgo: 11, weekday: 1, hour: 7, name: "Q3 capacity intro · US & EU", type: "new_leads", tone: "friendly", subject: "{{equipment}} capacity for {{company}} this quarter", goal: { type: "positive", target: 12 }, audience: { kinds: ["Broker", "Forwarder", "Shipper"] }, size: 70, open: 0.5, reply: 0.11, positive: 0.55, win: 0.3, followUps: 1 },
]

const REPLY_MIX = (type: string, positiveShare: number): [ReplyCategory, number][] => [
  ["interested", positiveShare * 0.6],
  ["rates", positiveShare * 0.4],
  ["not_now", (1 - positiveShare) * 0.4],
  ["out_of_office", (1 - positiveShare) * 0.15],
  ["not_interested", (1 - positiveShare) * (type === "winback" ? 0.35 : 0.3)],
  ["unsubscribe", (1 - positiveShare) * 0.15],
]

function followUpFor(i: number): FollowUp {
  return i === 0
    ? { afterDays: 3, subject: "Re: {{company}} capacity", body: "Hi {{first_name}},\n\nJust following up on my email below. We still have {{equipment}} trucks on {{lane}} this week. Would a quick rate help?\n\nBest regards,\n{{sender}}" }
    : { afterDays: 7, subject: "Last note from LJM International", body: "Hi {{first_name}},\n\nI don't want to fill your inbox, so this is my last note. If {{lane}} ever needs a reliable truck, just reply to this email.\n\nBest regards,\n{{sender}}" }
}

function toBrokerRecipient(b: Broker, st: BrokerStats): Recipient {
  return {
    id: b.id, kind: "broker", name: b.name, contactName: b.contact.name, email: b.contact.email, region: b.region,
    lane: st.topLane ?? `${b.lanes[0].origin} → ${b.lanes[0].destination}`, equipment: b.equipment[0], verified: true,
  }
}

function simulate(rng: Rng, def: Def, recipients: Recipient[], sentAt: number): CampaignRecipient[] {
  // weekday / hour effect on top of the campaign's own rates
  const timing = ([0.5, 0.85, 1.1, 1.05, 1.05, 0.7, 0.4][def.weekday]) * (def.hour <= 10 ? 1.08 : 0.85)
  const now = DEMO_NOW.getTime()
  return recipients.map((r) => {
    const base: CampaignRecipient = { ...r, status: "sent", at: sentAt, step: 1 }
    if (!r.verified && chance(rng, 0.3)) return { ...base, status: "bounced" }
    if (!chance(rng, def.open * timing)) {
      return { ...base, step: def.followUps ? 1 + int(rng, 0, def.followUps) : 1 }
    }
    // reply on the first email, or after one of the follow-ups
    const stepChances = [0.78, ...Array.from({ length: def.followUps }, (_, i) => (i === 0 ? 0.45 : 0.22))]
    for (let s = 0; s < stepChances.length; s++) {
      if (chance(rng, (def.reply / def.open) * timing * stepChances[s])) {
        const step = s + 1
        const offset = (s === 0 ? 0 : s === 1 ? 3 : 10) * DAY + int(rng, 1, 40) * HOUR
        const at = Math.min(now - HOUR, sentAt + offset)
        const category = weighted(rng, REPLY_MIX(def.type, def.positive))
        const reply = analyzeReply(makeReplyText(rng, category, r, CLIENT.dispatcher), at, step)
        const won = (reply.category === "interested" || reply.category === "rates") && chance(rng, def.win)
        return { ...base, status: "replied", at, step, reply, won, loads: won ? int(rng, 1, def.type === "dedicated_lane" ? 12 : 6) : 0 }
      }
    }
    return { ...base, status: "opened", step: 1 + def.followUps }
  })
}

export function buildCampaignHistory(brokers: Broker[], stats: Map<string, BrokerStats>): Campaign[] {
  const rng = createRng(90210)
  const used = new Set(brokers.map((b) => b.name))
  let n = 0

  return DEFS.map((def) => {
    // launch date: the requested weekday in the week `daysAgo` back
    const d = new Date(DEMO_NOW.getTime() - def.daysAgo * DAY)
    d.setUTCDate(d.getUTCDate() + ((def.weekday - d.getUTCDay() + 7) % 7) - 7)
    d.setUTCHours(def.hour, 0, 0, 0)
    const sentAt = d.getTime()

    let recipients: Recipient[] = []
    if (def.audience.existing) {
      recipients = brokers
        .filter((b) => stats.get(b.id)!.segment === def.audience.existing)
        .slice(0, def.size)
        .map((b) => toBrokerRecipient(b, stats.get(b.id)!))
    } else {
      // past prospects: companies the crawler found before this campaign
      for (let guard = 0; recipients.length < def.size && guard < 4000; guard++) {
        const l = makeLead(rng, ++n, used, new Date(sentAt - int(rng, 1, 20) * DAY))
        const a = def.audience
        if (a.kinds && !a.kinds.includes(l.kind)) continue
        if (a.region && l.region !== a.region) continue
        if (a.industry && !a.industry.includes(l.industry ?? "")) continue
        if (a.equipment && !l.equipment.includes(a.equipment as never)) continue
        recipients.push({
          id: `P${def.id}-${n}`, kind: "lead", companyType: l.kind, name: l.name, contactName: l.contact.name,
          email: l.contact.email, region: l.region, lane: `${l.lanes[0].origin} → ${l.lanes[0].destination}`,
          equipment: a.equipment ?? l.equipment[0], verified: l.emailVerified,
        })
      }
    }

    const tpl = draftTemplate({ campaign: def.type, tone: def.tone, region: "mixed", equipment: [], lanes: [] })
    const followUps = Array.from({ length: def.followUps }, (_, i) => followUpFor(i))
    const sentFollowUps = followUps.filter((f, i) => sentAt + followUps.slice(0, i + 1).reduce((s, x) => s + x.afterDays, 0) * DAY < DEMO_NOW.getTime()).length

    return {
      id: def.id,
      name: def.name,
      type: def.type,
      tone: def.tone,
      auto: false,
      historical: true,
      createdAt: sentAt,
      subject: def.subject,
      body: tpl.body,
      design: { layout: "branded", accent: def.type === "shipper_direct" ? "#2B2B2B" : "#BC2444", showLogo: true, showTruck: true, ctaLabel: def.type === "shipper_direct" ? "Book a trial load" : "Request a quote", ctaUrl: "https://ljminternational.com/quote", signature: true },
      goal: def.goal,
      schedule: { mode: "scheduled", at: sentAt },
      followUps,
      followUpsSent: sentFollowUps,
      recipients: simulate(rng, def, recipients, sentAt),
    } satisfies Campaign
  })
}
