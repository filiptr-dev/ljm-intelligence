import type { RejectionReason } from "@/lib/data/generate"
import type { Region } from "@/lib/data/geo"
import type { Email, Equipment, Lane } from "@/lib/data/types"

export type Intent =
  | "load_offer" | "capacity_offer" | "quote" | "booked" | "rejected" | "declined"
  | "invoice" | "payment_issue" | "complaint" | "praise" | "other"

export type { RejectionReason }

/** Structured output of the semantic analysis step, one per email. */
export type EmailInsight = {
  emailId: string
  intent: Intent
  sentiment: number // -1 … 1
  confidence: number // 0 … 1
  rate?: number
  perUnit?: number
  distance?: number
  currency?: "USD" | "EUR"
  lane?: Lane
  equipment?: Equipment
  rejectionReason?: RejectionReason
  evidence?: string // the phrase that drove the classification
}

export type BrokerSummaryInput = {
  name: string
  region: Region
  segment: string
  threads: number
  booked: number
  rejected: number
  winRate: number
  fleetWinRate: number
  revenue: number
  topLane?: string
  topReason?: string
  daysSinceLast: number
  avgResponseMin: number
  sentiment: number
  paymentIssues: number
  complaints: number
}

export type BrokerSummary = {
  headline: string
  summary: string
  risks: string[]
  nextAction: { label: string; detail: string; campaign: "new" | "reengage" | "winback" | "none" }
}

export type OutreachTone = "professional" | "friendly" | "direct"
export type CampaignType = "new_leads" | "shipper_direct" | "reengage" | "winback" | "dedicated_lane"
/** What a one-off email (outside a campaign) is about. */
export type EmailPurpose = "intro" | "check_in" | "truck_available" | "quote_followup" | "send_rate" | "rate_update" | "thank_you"

export type OutreachInput = {
  campaign: CampaignType | EmailPurpose
  tone: OutreachTone
  region: Region | "mixed"
  equipment: Equipment[]
  lanes: string[]
  /** optional plain-English description of the email the user wants */
  brief?: string
}

export type OutreachDraft = {
  subject: string
  body: string
  /** what the AI picked up from the brief, shown back to the user */
  understood?: string[]
}

export interface AIProvider {
  readonly name: string
  readonly model: string
  analyzeEmails(emails: Email[]): Promise<EmailInsight[]>
  summarizeBroker(input: BrokerSummaryInput): Promise<BrokerSummary>
  draftOutreach(input: OutreachInput): Promise<OutreachDraft>
}
