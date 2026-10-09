/**
 * Brokers domain module — thin wrappers over the typed openapi-fetch client.
 *
 * One module per domain (bb-unikoop pattern). Shapes come from the generated
 * `schema.d.ts` — regenerate via `pnpm gen:api` whenever the backend changes
 * and let `tsc` catch the callers.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type ContactField = components["schemas"]["ContactFieldOut"]
export type NextActionOut = components["schemas"]["NextActionOut"]
export type NamedContact = components["schemas"]["NamedContactOut"]
export type BrokerRow = components["schemas"]["BrokerRowOut"]
// AI "what next" surface. Carried on every list row so the brokers table can
// show the cached next_step without touching the LLM. Status disambiguates
// "no summary yet → Generate button" from "summary ready but next_step null →
// nothing to say" (legacy pre-0033 rows).
export type LeadNextStep = components["schemas"]["LeadNextStepOut"]
export type AiSummaryStatus = BrokerRow["ai_summary_status"]
export type LeadAiSummary = components["schemas"]["LeadAiSummaryOut"]
export type BrokerList = components["schemas"]["BrokerListOut"]
export type BrokerDetail = components["schemas"]["BrokerDetailOut"]
export type OverviewMetrics = components["schemas"]["OverviewMetricsOut"]
export type MonthlyPoint = components["schemas"]["MonthlyPointOut"]
export type SegmentsCount = components["schemas"]["SegmentsCountOut"]
export type OverviewSummary = components["schemas"]["OverviewSummaryOut"]
export type BrokerSegment = NonNullable<OverviewMetrics>["segment"]
export type BrokerSortKey = "health" | "win_rate" | "booked" | "rejected" | "days_since" | "name"
export type ActivityEvent =
  | components["schemas"]["ActivityCallOut"]
  | components["schemas"]["ActivityEmailOut"]
export type ActivityPage = components["schemas"]["ActivityPageOut"]
export type ObjectionItem = components["schemas"]["ObjectionItemOut"]
export type Objections = components["schemas"]["ObjectionsOut"]

export type NextActionKind = NextActionOut["kind"]

export type ListBrokersQuery = {
  state?: string
  min_fit?: number
  has_email?: boolean
  has_phone?: boolean
  next_action?: NextActionKind
  q?: string
  cursor?: string
  limit?: number
  include?: string
  segment?: BrokerSegment | "all"
  sort?: BrokerSortKey
}

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

/** GET /brokers — ranked list with next-action chip per row. */
export async function listBrokers(
  query: ListBrokersQuery = {},
  signal?: AbortSignal,
): Promise<BrokerList> {
  const res = await api.GET("/brokers", { params: { query }, signal })
  return unwrap(res, "/brokers")
}

export type OverviewSummaryQuery = {
  state?: string
  min_fit?: number
  has_email?: boolean
  has_phone?: boolean
  next_action?: NextActionKind
  q?: string
  segment?: BrokerSegment | "all"
  sort?: BrokerSortKey
}

/** GET /brokers/overview-summary — companion payload for the brokers island.
 *
 * Shares every filter with ``listBrokers``. Returns ``{items: {leadId:
 * OverviewMetrics}, segments_count}`` so the UI can fire this in parallel
 * with the fast list fetch and splice overview columns onto already-rendered
 * rows without blocking first paint on the slow aggregate. */
export async function listBrokersOverviewSummary(
  query: OverviewSummaryQuery = {},
  signal?: AbortSignal,
): Promise<OverviewSummary> {
  const res = await api.GET("/brokers/overview-summary", { params: { query }, signal })
  return unwrap(res, "/brokers/overview-summary")
}

/** GET /brokers/{id} — full contact block + computed next action + timeline. */
export async function getBroker(id: string, signal?: AbortSignal): Promise<BrokerDetail> {
  const res = await api.GET("/brokers/{broker_id}", {
    params: { path: { broker_id: id } },
    signal,
  })
  return unwrap(res, `/brokers/${id}`)
}

/** GET /brokers/{id}/activity — paged timeline. */
export async function getBrokerActivity(
  id: string,
  cursor?: string,
  limit = 50,
  signal?: AbortSignal,
): Promise<ActivityPage> {
  const res = await api.GET("/brokers/{broker_id}/activity", {
    params: { path: { broker_id: id }, query: { cursor, limit } },
    signal,
  })
  return unwrap(res, `/brokers/${id}/activity`)
}

/** GET /brokers/{id}/objections — "Why they said no" roll-up. */
export async function getBrokerObjections(
  id: string,
  signal?: AbortSignal,
): Promise<Objections> {
  const res = await api.GET("/brokers/{broker_id}/objections", {
    params: { path: { broker_id: id } },
    signal,
  })
  return unwrap(res, `/brokers/${id}/objections`)
}

/** POST-equivalent — triggers (or returns cached) AI summary for one lead.
 *
 * Thin wrapper over the detail-page endpoint (``GET /analysis/lead/{id}``)
 * so the brokers list can call the same code path. Reuses the server-side
 * cache, provider resolution, and empty/unavailable states — one shape, one
 * place to evolve. Keeps the ``lib/api/`` typed openapi-fetch pattern (no
 * ad-hoc ``/api`` proxies). */
export async function generateLeadAiSummary(
  leadId: string,
  signal?: AbortSignal,
): Promise<LeadAiSummary> {
  const res = await api.GET("/analysis/lead/{lead_id}", {
    params: { path: { lead_id: leadId }, query: { refresh: false } },
    signal,
  })
  return unwrap(res, `/analysis/lead/${leadId}`)
}
