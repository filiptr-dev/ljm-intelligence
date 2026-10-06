"use client"

import * as React from "react"
import type { EmailPurpose, OutreachTone } from "@/lib/ai/types"
import { PURPOSE_LABEL, type Campaign, type EmailDesign, type Recipient } from "@/lib/campaigns/types"
import { type Lead } from "@/lib/data/types"

/**
 * Client-side campaign store (v1).
 *
 * Pre-2026-10-06, this file also ran a client-side timer fiction that:
 *   - streamed invented crawler/scan/found/verify/outreach/reply events,
 *   - appended fake "live leads" to the real pool,
 *   - flipped every recipient queued → sent → opened → replied → won on a 1s
 *     timer so a brand-new campaign on an empty DB "showed replies".
 *
 * That whole simulation is gone. The engine is now just a thin store for
 * campaigns the user composes in-browser (they persist to localStorage;
 * actually dispatching them to the real queue is a separate ticket). The
 * monitoring pill, the live feed and the honest per-recipient delivery
 * state are backend reads (`useTodayCounters`, `useLiveFeed`,
 * `useCampaignStatus`). See plan 2026-10-06.
 */

export type { Campaign, CampaignRecipient, EmailDesign, Recipient, RecipientStatus } from "@/lib/campaigns/types"

type Engine = {
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
  /** the builder's design; omitted = plain personal email, as before */
  design?: EmailDesign
}

const EngineContext = React.createContext<Engine | null>(null)

export function useEngine() {
  const ctx = React.useContext(EngineContext)
  if (!ctx) throw new Error("useEngine must be used inside <EngineProvider>")
  return ctx
}

export const DEFAULT_DESIGN: EmailDesign = {
  layout: "branded",
  accent: "#BC2444",
  showLogo: true,
  showTruck: true,
  ctaLabel: "Request a quote",
  ctaUrl: "https://ljminternational.com/quote",
  signature: true,
}

export const PLAIN_DESIGN: EmailDesign = { ...DEFAULT_DESIGN, layout: "plain", showLogo: false, showTruck: false, ctaLabel: "" }

// Bump on brand change so a stale pre-rebrand localStorage on Filip's box
// doesn't confuse a first-meeting reload.
const STORAGE_KEY = "ljm.campaigns.v1"

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

export function EngineProvider({ children }: { children: React.ReactNode }) {
  const [campaigns, setCampaigns] = React.useState<Campaign[]>([])

  // restore campaigns across reloads; never save before the restore has happened
  const [restored, setRestored] = React.useState(false)
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
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

  const campaignsRef = React.useRef(campaigns)
  React.useEffect(() => {
    campaignsRef.current = campaigns
  }, [campaigns])

  const contacted = React.useMemo(() => new Set(campaigns.flatMap((c) => c.recipients.map((r) => r.id))), [campaigns])

  const sendCampaign: Engine["sendCampaign"] = React.useCallback(
    (c) => {
      const id = `c${Date.now()}`
      const at = Date.now()
      // Recipients enter 'queued' and STAY queued — the client no longer
      // flips them to sent/opened/replied on a timer. Real delivery state
      // comes from `useCampaignStatus` (POST /analysis/campaign-status),
      // which reads sent_log + mail_messages for the tenant.
      enqueue({ ...c, id, auto: false, createdAt: at, followUpsSent: 0, recipients: c.recipients.map((r) => ({ ...r, status: "queued", at, step: 1 })) })
      return id
    },
    [enqueue],
  )

  const sendFollowUp = React.useCallback(
    (id: string) => {
      const c = campaignsRef.current.find((x) => x.id === id)
      if (!c) return 0
      const nextStep = (c.followUpsSent ?? 0) + 2
      // Follow-ups target recipients that haven't been marked replied
      // (reply state now comes from the backend-backed status map; here
      // we just re-queue them so the sender can send a real follow-up
      // when the queue path lands).
      const targets = c.recipients.filter((r) => !r.reply)
      const ids = new Set(targets.map((r) => r.id))
      const at = Date.now()
      setCampaigns((prev) =>
        prev.map((x) =>
          x.id === id
            ? {
                ...x,
                followUpsSent: (x.followUpsSent ?? 0) + 1,
                recipients: x.recipients.map((r) => (ids.has(r.id) ? { ...r, status: "queued", at, step: nextStep } : r)),
              }
            : x,
        ),
      )
      return targets.length
    },
    [],
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
        design: e.design ?? PLAIN_DESIGN,
        schedule: { mode: e.at > createdAt + 60_000 ? "scheduled" : "now", at: e.at },
        followUps: e.followUpDays ? [{ afterDays: e.followUpDays, subject: `Re: ${e.subject}`, body: "Hi {{first_name}},\n\nJust bringing this back to the top of your inbox. Let me know either way.\n\nBest regards,\n{{sender}}" }] : [],
        followUpsSent: 0,
        recipients: [{ ...e.recipient, status: "queued", at: createdAt, step: 1 }],
      })
      return id
    },
    [enqueue],
  )

  const value = React.useMemo(
    () => ({ campaigns, contacted, sendCampaign, sendFollowUp, sendEmail }),
    [campaigns, contacted, sendCampaign, sendFollowUp, sendEmail],
  )
  return <EngineContext.Provider value={value}>{children}</EngineContext.Provider>
}
