/**
 * Typed client for the LJM Intelligence FastAPI backend.
 *
 * Two rules:
 *   - Only the SERVER-side helpers touch `CRON_SECRET`. The browser never sees it.
 *   - Every call has an upper timeout so a sleeping Render free-tier instance
 *     can't hang the UI; readers fall back to the simulated feed instead.
 *
 * `BACKEND_URL` is read on the server (Next.js `process.env.BACKEND_URL`), and
 * `NEXT_PUBLIC_API_URL` is the browser-safe mirror (unused today because we
 * proxy everything through `/api/crawler/*` — keeps CORS + secrets simple).
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

const DEFAULT_BACKEND = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"

async function fetchJson<T>(url: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const { timeoutMs = 6000, ...rest } = init
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(url, { ...rest, signal: ctrl.signal, cache: "no-store" })
    if (!res.ok) throw new Error(`backend ${res.status} ${res.statusText}`)
    return (await res.json()) as T
  } finally {
    clearTimeout(t)
  }
}

/** Merge a bearer token into a headers init object. Centralised so every
 *  `backend.*` call stamps `Authorization` the same way; keep in sync with
 *  `@/lib/auth/bff#authHeaders` (same shape, different signature). */
function withAuth(extra: HeadersInit | undefined, authToken: string | undefined): HeadersInit | undefined {
  if (!authToken) return extra
  const h = new Headers(extra ?? {})
  if (!h.has("authorization")) h.set("authorization", `Bearer ${authToken}`)
  return h
}

export const backend = {
  base: DEFAULT_BACKEND,
  async health(base = DEFAULT_BACKEND): Promise<{ ok: boolean; db: "up" | "down" }> {
    return fetchJson(`${base}/health`, { timeoutMs: 4000 })
  },
  async listLeads(base = DEFAULT_BACKEND, params: { limit?: number; state?: string; kind?: string; min_score?: number } = {}, authToken?: string) {
    const qs = new URLSearchParams()
    if (params.limit) qs.set("limit", String(params.limit))
    if (params.state) qs.set("state", params.state)
    if (params.kind) qs.set("kind", params.kind)
    if (params.min_score !== undefined) qs.set("min_score", String(params.min_score))
    return fetchJson<BackendLeadsPage>(`${base}/leads?${qs.toString()}`, {
      headers: withAuth(undefined, authToken),
      timeoutMs: 8000,
    })
  },
  async latestRun(base = DEFAULT_BACKEND, authToken?: string): Promise<BackendCrawlRun | null> {
    return fetchJson<BackendCrawlRun | null>(`${base}/crawl/latest`, {
      headers: withAuth(undefined, authToken),
      timeoutMs: 5000,
    })
  },
  /** SERVER-only. Uses CRON_SECRET from the process env; never call from the browser. */
  async triggerRun(base = DEFAULT_BACKEND, limit = 25): Promise<{ run_id: string; status: string }> {
    const secret = process.env.CRON_SECRET || ""
    return fetchJson(`${base}/crawl/run?trigger=on_demand&limit=${limit}`, {
      method: "POST",
      headers: { "X-Cron-Secret": secret },
      timeoutMs: 8000,
    })
  },
  async draftEmail(
    base = DEFAULT_BACKEND,
    body: { lead_id?: string; lead?: Record<string, unknown>; tone: EmailTone; instructions?: string },
    authToken?: string,
  ) {
    return fetchJson<EmailDraft>(`${base}/email/draft`, {
      method: "POST",
      headers: withAuth({ "Content-Type": "application/json" }, authToken),
      body: JSON.stringify(body),
      timeoutMs: 45000,
    })
  },
}
