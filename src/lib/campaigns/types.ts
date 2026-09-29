import type { CampaignType, EmailPurpose, OutreachTone } from "@/lib/ai/types"
import type { Region } from "@/lib/data/geo"
import type { LeadKind } from "@/lib/data/types"

export type Recipient = {
  id: string
  kind: "lead" | "broker"
  /** what the company is: freight broker, direct shipper, forwarder */
  companyType?: LeadKind
  name: string
  contactName: string
  email: string
  region: Region
  lane?: string
  equipment?: string
  verified?: boolean
}

export type RecipientStatus = "queued" | "sent" | "opened" | "replied" | "bounced"

export type EmailDesign = {
  layout: "plain" | "branded" | "card"
  accent: string
  showLogo: boolean
  showTruck: boolean
  ctaLabel: string
  ctaUrl: string
  signature: boolean
}

export type GoalType = "replies" | "positive" | "accounts" | "loads"
export const GOAL_LABEL: Record<GoalType, string> = {
  replies: "Replies",
  positive: "Interested replies",
  accounts: "New customers won",
  loads: "Loads booked",
}
export type Goal = { type: GoalType; target: number }

export type Schedule = { mode: "now" | "peak" | "scheduled"; at: number }

export type FollowUp = { afterDays: number; subject: string; body: string }

export type ReplyCategory = "interested" | "rates" | "not_now" | "out_of_office" | "not_interested" | "unsubscribe"
export const REPLY_LABEL: Record<ReplyCategory, string> = {
  interested: "Interested",
  rates: "Asked for rates",
  not_now: "Not right now",
  out_of_office: "Out of office",
  not_interested: "Not interested",
  unsubscribe: "Unsubscribe",
}
/** Ordered from best to worst outcome; colours read positive → negative. */
export const REPLY_COLOR: Record<ReplyCategory, string> = {
  interested: "var(--good)",
  rates: "var(--chart-1)",
  not_now: "var(--steel)",
  out_of_office: "var(--chrome)",
  not_interested: "var(--chart-2)",
  unsubscribe: "var(--bad)",
}
export const POSITIVE: ReplyCategory[] = ["interested", "rates"]

export type Reply = {
  text: string
  category: ReplyCategory
  sentiment: number
  confidence: number
  at: number
  step: number
}

export type CampaignRecipient = Recipient & {
  status: RecipientStatus
  at: number
  /** which email of the sequence they last received (1 = first email) */
  step?: number
  reply?: Reply
  won?: boolean
  loads?: number
}

export type Campaign = {
  id: string
  name: string
  type: CampaignType | string
  tone?: OutreachTone
  /** the user's own description of the email, when the AI wrote it from a brief */
  brief?: string
  auto: boolean
  /** a one-off email to one company, sent outside of a campaign */
  single?: boolean
  historical?: boolean
  createdAt: number
  subject: string
  body: string
  design: EmailDesign
  goal?: Goal
  schedule?: Schedule
  followUps?: FollowUp[]
  followUpsSent?: number
  recipients: CampaignRecipient[]
}

export const CAMPAIGN_TYPE_LABEL: Record<string, string> = {
  new_leads: "Capacity intro",
  shipper_direct: "Direct shipper intro",
  reengage: "Re-engagement",
  winback: "Win-back on price",
  dedicated_lane: "Dedicated lane offer",
  capacity_alert: "Capacity alert",
}

export const PURPOSE_LABEL: Record<EmailPurpose, string> = {
  intro: "Introduction",
  check_in: "Checking in",
  truck_available: "Truck available",
  quote_followup: "Quote follow-up",
  send_rate: "Send a rate",
  rate_update: "New rates",
  thank_you: "Thank you",
}

export const TONE_LABEL: Record<OutreachTone, string> = { professional: "Professional", friendly: "Friendly", direct: "Direct" }
