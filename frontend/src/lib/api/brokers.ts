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
