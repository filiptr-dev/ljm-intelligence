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
