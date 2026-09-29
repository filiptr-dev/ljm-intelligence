"use client"

import * as React from "react"
import { lookalikeScore, type LookalikeProfile } from "@/lib/analytics/similarity"
import type { Region } from "@/lib/data/geo"
import { makeLead, SOURCE_LIST } from "@/lib/data/leads"
import { createRng, pick } from "@/lib/data/rng"
import { analyzeReply, makeReplyText } from "@/lib/ai/replies"
import type { EmailPurpose, OutreachTone } from "@/lib/ai/types"
import { PURPOSE_LABEL, type Campaign, type CampaignRecipient, type EmailDesign, type Recipient, type RecipientStatus, type Reply, type ReplyCategory } from "@/lib/campaigns/types"
import { CLIENT, LEAD_KIND_LABEL, type Lead } from "@/lib/data/types"

/**
 * Client-side "always on" engine: streams crawler activity, discovers leads,
 * runs auto-outreach and simulates delivery / opens / replies for campaigns.
 */

export type FeedEvent = {
  id: number
  at: number
  kind: "scan" | "found" | "verify" | "duplicate" | "outreach" | "reply"
  text: string
  detail?: string
  score?: number
  region?: Region
}

export type { Campaign, CampaignRecipient, EmailDesign, Recipient, RecipientStatus } from "@/lib/campaigns/types"

type Engine = {
  feed: FeedEvent[]
  liveLeads: Lead[]
  counters: { scanned: number; found: number; sent: number; replies: number }
  autoOutreach: boolean
  setAutoOutreach: (v: boolean) => void
  campaigns: Campaign[]
  contacted: Set<string>
  sendCampaign: (c: Omit<Campaign, "id" | "createdAt" | "recipients" | "auto"> & { recipients: Recipient[] }) => string
  /** send the next follow-up step now to everyone who hasn't replied */
  sendFollowUp: (id: string) => number
  /** a one-off email to one company, outside of any campaign */
  sendEmail: (e: OneOffEmail) => string
}

export type OneOffEmail = {
  recipient: Recipient
  purpose: EmailPurpose
  tone: OutreachTone
  brief?: string
  subject: string
  body: string
  at: number
  followUpDays?: number
}

const EngineContext = React.createContext<Engine | null>(null)

export function useEngine() {
  const ctx = React.useContext(EngineContext)
  if (!ctx) throw new Error("useEngine must be used inside <EngineProvider>")
  return ctx
}

export const DEFAULT_DESIGN: EmailDesign = {
  layout: "branded",
  accent: "#f5b800",
  showLogo: true,
  showTruck: true,
  ctaLabel: "Request a quote",
  ctaUrl: "https://ironline-transport.com/quote",
  signature: true,
}

const AUTO_SUBJECT = "{{equipment}} capacity for {{company}}"
const AUTO_BODY =
  "Hi {{first_name}},\n\nWe run {{equipment}} trucks daily on {{lane}} and have capacity this week. We answer quotes within 15 minutes.\n\nBest regards,\n{{sender}}"

const PAGES: Record<string, number> = {}
const STORAGE_KEY = "freightradar.campaigns.v3"

export const PLAIN_DESIGN: EmailDesign = { ...DEFAULT_DESIGN, layout: "plain", showLogo: false, showTruck: false, ctaLabel: "" }

/** Live reply mix: direct shippers, dedicated offers and personal one-off emails get warmer answers. */
function pickCategory(type: string, single = false): ReplyCategory {
  const positive = single ? 0.66 : type === "shipper_direct" || type === "dedicated_lane" ? 0.65 : type === "winback" ? 0.42 : 0.52
  const r = Math.random()
  if (r < positive * 0.6) return "interested"
  if (r < positive) return "rates"
  const rest = (r - positive) / (1 - positive)
  return rest < 0.4 ? "not_now" : rest < 0.55 ? "out_of_office" : rest < 0.85 ? "not_interested" : "unsubscribe"
}

export function toRecipient(l: Lead): Recipient {
  return {
    id: l.id,
    kind: "lead",
    companyType: l.kind,
    name: l.name,
    contactName: l.contact.name,
    email: l.contact.email,
    region: l.region,
    lane: `${l.lanes[0].origin} → ${l.lanes[0].destination}`,
    equipment: l.equipment[0],
    verified: l.emailVerified,
  }
}

export function EngineProvider({
  children,
  profile,
  knownNames,
  baseline,
  seed,
}: {
  children: React.ReactNode
  profile: LookalikeProfile
  knownNames: string[]
  baseline: { scanned: number; found: number; sent: number; replies: number }
  seed: { at: number; text: string; detail: string; score: number; region: Region }[]
}) {
  const [feed, setFeed] = React.useState<FeedEvent[]>(() =>
    seed.map((e, i) => ({ ...e, kind: "found" as const, id: -i - 1 })),
  )
  const [liveLeads, setLiveLeads] = React.useState<Lead[]>([])
  const [counters, setCounters] = React.useState(baseline)
  const [autoOutreach, setAutoOutreach] = React.useState(true)
  const [campaigns, setCampaigns] = React.useState<Campaign[]>([])
  const autoRef = React.useRef(autoOutreach)
  React.useEffect(() => {
    autoRef.current = autoOutreach
  }, [autoOutreach])

  const eventId = React.useRef(0)
  const liveCounter = React.useRef(0)
  const push = React.useCallback((e: Omit<FeedEvent, "id" | "at">) => {
    setFeed((f) => [{ ...e, id: ++eventId.current, at: Date.now() }, ...f].slice(0, 80))
  }, [])

  // restore campaigns across reloads; never save before the restore has happened
  const [restored, setRestored] = React.useState(false)
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      // restored after mount so server and first client render match
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (raw) setCampaigns(JSON.parse(raw))
    } catch {}
    setRestored(true)
  }, [])
  React.useEffect(() => {
    if (!restored) return
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(campaigns.slice(0, 60)))
    } catch {}
  }, [campaigns, restored])

  const enqueue = React.useCallback((campaign: Campaign) => {
    setCampaigns((cs) => {
      if (campaign.auto) {
        const existing = cs.find((c) => c.auto)
        if (existing) {
          const known = new Set(existing.recipients.map((r) => r.id))
          const fresh = campaign.recipients.filter((r) => !known.has(r.id))
          return cs.map((c) => (c === existing ? { ...c, recipients: [...fresh, ...c.recipients] } : c))
        }
      }
      return [campaign, ...cs]
    })
  }, [])

  // crawler loop
  React.useEffect(() => {
    const rng = createRng(Date.now() % 100000)
    const session = Date.now().toString(36)
    const used = new Set(knownNames)
    let timer: ReturnType<typeof setTimeout>
    let stopped = false

    const tick = () => {
      if (stopped) return
      const r = rng()
      // realistic pace: a scan every ~7–16s, a genuinely new broker roughly once a minute
      if (r < 0.2) {
        // ids must stay unique across restarts and across sessions saved in localStorage
        const n = ++liveCounter.current
        const lead = { ...makeLead(rng, n, used, new Date()), id: `LIVE-${session}-${n}` }
        const scored: Lead = { ...lead, score: lookalikeScore(lead, profile) }
        setLiveLeads((ls) => [scored, ...ls])
        setCounters((c) => ({ ...c, found: c.found + 1, scanned: c.scanned + 1 }))
        push({
          kind: "found",
          text: scored.name,
          detail: `${LEAD_KIND_LABEL[scored.kind]}${scored.industry ? ` · ${scored.industry}` : ""} · ${scored.hq} · via ${scored.source}`,
          score: scored.score,
          region: scored.region,
        })
        if (scored.emailVerified) {
          setTimeout(() => push({ kind: "verify", text: `Email verified: ${scored.contact.email}`, region: scored.region }), 4000 + rng() * 5000)
        }
        if (autoRef.current && scored.emailVerified && scored.score >= 70) {
          setTimeout(() => {
            enqueue({
              id: "auto",
              name: "Auto-outreach · new high-match leads",
              type: "new_leads",
              auto: true,
              createdAt: Date.now(),
              subject: AUTO_SUBJECT,
              body: AUTO_BODY,
              design: DEFAULT_DESIGN,
              recipients: [{ ...toRecipient(scored), status: "queued", at: Date.now(), step: 1 }],
            })
            push({ kind: "outreach", text: `Auto-outreach queued → ${scored.contact.name}`, detail: scored.name, region: scored.region })
          }, 25_000 + rng() * 25_000)
        }
      } else if (r < 0.32) {
        push({ kind: "duplicate", text: `Skipped ${pick(rng, knownNames)}`, detail: "Already in your broker database" })
        setCounters((c) => ({ ...c, scanned: c.scanned + 1 }))
      } else {
        const region: Region = rng() < 0.6 ? "US" : "EU"
        const source = pick(rng, SOURCE_LIST[region])
        PAGES[source] = (PAGES[source] ?? Math.floor(rng() * 400) + 40) + 1
        const batch = Math.floor(rng() * 20) + 6
        setCounters((c) => ({ ...c, scanned: c.scanned + batch }))
        push({ kind: "scan", text: `Scanning ${source}`, detail: `page ${PAGES[source]} · ${batch} companies checked`, region })
      }
      timer = setTimeout(tick, 7000 + rng() * 9000)
    }
    timer = setTimeout(tick, 2500)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [knownNames, profile, push, enqueue])

  // delivery simulation: queued → sent → opened → replied
  const campaignsRef = React.useRef(campaigns)
  React.useEffect(() => {
    campaignsRef.current = campaigns
  }, [campaigns])
  React.useEffect(() => {
    const rng = createRng(Date.now() % 7919)
    const id = setInterval(() => {
      const now = Date.now()
      let sentNow = 0
      const replies: string[] = []
      const wins: string[] = []
      const updates = new Map<string, Partial<CampaignRecipient>>()
      for (const c of campaignsRef.current) {
        if (c.historical) continue
        if (c.schedule && c.schedule.at > now) continue // scheduled for later
        // provider-style throttling: about one email every two seconds per campaign
        let sendBudget = Math.random() < 0.5 ? 1 : 0
        for (const r of c.recipients) {
          const age = now - r.at
          const key = `${c.id}:${r.id}`
          if (r.status === "queued" && sendBudget > 0) {
            sendBudget--
            sentNow++
            updates.set(key, { status: r.verified === false && Math.random() < 0.35 ? "bounced" : "sent", at: now })
          } else if (r.status === "sent" && age > (c.single ? 10_000 : 30_000) && Math.random() < (c.single ? 0.04 : 0.006)) {
            updates.set(key, { status: "opened", at: now })
          } else if (r.status === "opened" && !r.reply && age > (c.single ? 20_000 : 90_000) && Math.random() < (c.single ? 0.012 : 0.001 * ((r.step ?? 1) > 1 ? 1.6 : 1))) {
            const category = pickCategory(c.type, c.single)
            const reply: Reply = analyzeReply(makeReplyText(rng, category, r, CLIENT.dispatcher), now, r.step ?? 1)
            updates.set(key, { status: "replied", at: now, reply })
            replies.push(`${r.contactName} · ${r.name} (${reply.category.replace("_", " ")})`)
          } else if (r.reply && !r.won && (r.reply.category === "interested" || r.reply.category === "rates") && age > 60_000 && Math.random() < 0.004) {
            updates.set(key, { won: true, loads: 1 + Math.floor(Math.random() * 4), at: now })
            wins.push(r.name)
          }
        }
      }
      if (!updates.size) return
      setCampaigns((prev) =>
        prev.map((c) =>
          c.recipients.some((r) => updates.has(`${c.id}:${r.id}`))
            ? { ...c, recipients: c.recipients.map((r) => ({ ...r, ...updates.get(`${c.id}:${r.id}`) })) }
            : c,
        ),
      )
      if (sentNow) setCounters((c) => ({ ...c, sent: c.sent + sentNow }))
      if (replies.length) {
        setCounters((c) => ({ ...c, replies: c.replies + replies.length }))
        replies.forEach((r) => push({ kind: "reply", text: `Reply received: ${r}` }))
      }
      wins.forEach((w) => push({ kind: "reply", text: `New customer won: ${w}`, detail: "First load booked from a campaign reply" }))
    }, 1000)
    return () => clearInterval(id)
  }, [push])

  const contacted = React.useMemo(() => new Set(campaigns.flatMap((c) => c.recipients.map((r) => r.id))), [campaigns])

  const sendCampaign: Engine["sendCampaign"] = React.useCallback(
    (c) => {
      const id = `c${Date.now()}`
      const at = Date.now()
      enqueue({ ...c, id, auto: false, createdAt: at, followUpsSent: 0, recipients: c.recipients.map((r) => ({ ...r, status: "queued", at, step: 1 })) })
      const later = c.schedule && c.schedule.at > at
      push({ kind: "outreach", text: `Campaign ${later ? "scheduled" : "started"}: ${c.name}`, detail: `${c.recipients.length} recipients` })
      return id
    },
    [enqueue, push],
  )

  const sendFollowUp = React.useCallback(
    (id: string) => {
      const c = campaignsRef.current.find((x) => x.id === id)
      if (!c) return 0
      const nextStep = (c.followUpsSent ?? 0) + 2
      const targets = c.recipients.filter((r) => (r.status === "sent" || r.status === "opened") && !r.reply)
      const ids = new Set(targets.map((r) => r.id))
      const at = Date.now()
      setCampaigns((prev) =>
        prev.map((x) =>
          x.id === id
            ? {
                ...x,
                followUpsSent: (x.followUpsSent ?? 0) + 1,
                recipients: x.recipients.map((r) => (ids.has(r.id) ? { ...r, status: "queued" as RecipientStatus, at, step: nextStep } : r)),
              }
            : x,
        ),
      )
      push({ kind: "outreach", text: `Follow-up ${nextStep - 1} sent: ${c.name}`, detail: `${targets.length} people who haven't replied` })
      return targets.length
    },
    [push],
  )

  const sendEmail = React.useCallback(
    (e: OneOffEmail) => {
      const id = `e${Date.now()}`
      const createdAt = Date.now()
      enqueue({
        id,
        name: `${PURPOSE_LABEL[e.purpose]} · ${e.recipient.name}`,
        type: e.purpose,
        tone: e.tone,
        brief: e.brief,
        auto: false,
        single: true,
        createdAt,
        subject: e.subject,
        body: e.body,
        design: PLAIN_DESIGN,
        schedule: { mode: e.at > createdAt + 60_000 ? "scheduled" : "now", at: e.at },
        followUps: e.followUpDays ? [{ afterDays: e.followUpDays, subject: `Re: ${e.subject}`, body: "Hi {{first_name}},\n\nJust bringing this back to the top of your inbox. Let me know either way.\n\nBest regards,\n{{sender}}" }] : [],
        followUpsSent: 0,
        recipients: [{ ...e.recipient, status: "queued", at: createdAt, step: 1 }],
      })
      push({ kind: "outreach", text: `Email ${e.at > createdAt + 60_000 ? "scheduled" : "sent"} → ${e.recipient.contactName}`, detail: `${e.recipient.name} · ${PURPOSE_LABEL[e.purpose]}` })
      return id
    },
    [enqueue, push],
  )

  const value = React.useMemo(
    () => ({ feed, liveLeads, counters, autoOutreach, setAutoOutreach, campaigns, contacted, sendCampaign, sendFollowUp, sendEmail }),
    [feed, liveLeads, counters, autoOutreach, campaigns, contacted, sendCampaign, sendFollowUp, sendEmail],
  )
  return <EngineContext.Provider value={value}>{children}</EngineContext.Provider>
}
