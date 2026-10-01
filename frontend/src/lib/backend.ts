/**
 * Shared **type-only** surface for the LJM Intelligence FastAPI backend.
 *
 * The live `fetch` helpers that used to live here (`triggerRun`, `listLeads`,
 * `latestRun`, `draftEmail`, `health`) were removed when the frontend moved
 * to server-first Next.js: every HTTP call now goes through the typed
 * `lib/api/` client server-side, or through `/api/proxy/[...path]` for
 * client islands. Keeping this file as a types-only module avoids a parallel
 * fetch surface — one data path, enforced by the `server-only` import guard
 * on `lib/api/`.
 */

export type BackendLead = {
  id: string
  kind: "Broker" | "Shipper" | "Forwarder"
  name: string
  state: string
  city: string | null
  mc: string | null
  dot: string | null
  domain: string | null
  primary_email: string | null
  phone: string | null
  current_score: number | null
  first_seen_at: string
  last_seen_at: string
  sources: string[]
}

export type BackendLeadsPage = {
  items: BackendLead[]
  total: number
  limit: number
  offset: number
}

export type BackendCrawlRun = {
  id: string
  status: "queued" | "running" | "done" | "error"
  kind: string
  trigger: "cron" | "on_demand"
  started_at: string
  finished_at: string | null
  counts: {
    discovered?: number
    new?: number
    scored?: number
    auto_contacted?: number
    gemini_discovered?: number
    gemini_new?: number
    errors?: number
  }
  error: string | null
}

export type EmailDraft = {
  subject: string
  body: string
  body_html: string
  source: "gemini" | "claude" | "fallback"
  tone: EmailTone
  lead_id: string | null
  /** angle the draft took from the broker's sentiment; null for cold leads */
  stance?: EmailStance | null
}

export type EmailStance = "positive" | "neutral" | "cooling"

export const STANCE_LABEL: Record<EmailStance, string> = {
  positive: "Availability pitch (warm relationship)",
  neutral: "Friendly check-in (neutral relationship)",
  cooling: "Soft re-engage (relationship cooling)",
}

export const EMAIL_TONES = ["professional", "friendly", "direct", "persuasive"] as const
export type EmailTone = (typeof EMAIL_TONES)[number]

export const TONE_LABEL: Record<EmailTone, string> = {
  professional: "Professional",
  friendly: "Friendly",
  direct: "Direct",
  persuasive: "Persuasive",
}
