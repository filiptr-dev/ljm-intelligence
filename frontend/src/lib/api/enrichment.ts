/**
 * Enrichment domain module — mirrors `shipper-finder.ts` (bb-unikoop pattern).
 *
 * Every call goes through the typed openapi-fetch client on `NEXT_PUBLIC_API_URL`.
 * No hand-typed shapes; no `any`. Regenerate `schema.d.ts` via `pnpm gen:api`
 * whenever the backend contract changes and let `tsc` catch the callers.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type EnrichmentResult = components["schemas"]["EnrichmentResultOut"]
export type EnrichmentMetrics = components["schemas"]["EnrichmentMetricsOut"]
export type DecisionMaker = components["schemas"]["DecisionMakerOut"]
export type WebsiteContact = components["schemas"]["WebsiteContactOut"]
export type AutoSendResult = components["schemas"]["AutoSendOut"]
export type UnsubscribeResult = components["schemas"]["UnsubscribeOut"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function enrichLead(leadId: string, opts: { force?: boolean } = {}): Promise<EnrichmentResult> {
  const res = await api.POST("/enrichment/leads/{lead_id}", {
    params: { path: { lead_id: leadId }, query: { force: opts.force ?? false } },
  })
  return unwrap(res, `/enrichment/leads/${leadId}`)
}

export async function enrichCandidate(
  candidateId: string,
  opts: { force?: boolean } = {},
): Promise<EnrichmentResult> {
  const res = await api.POST("/enrichment/candidates/{candidate_id}", {
    params: { path: { candidate_id: candidateId }, query: { force: opts.force ?? false } },
  })
  return unwrap(res, `/enrichment/candidates/${candidateId}`)
}

export async function getEnrichment(leadId: string): Promise<EnrichmentResult> {
  const res = await api.GET("/enrichment/leads/{lead_id}", {
    params: { path: { lead_id: leadId } },
  })
  return unwrap(res, `/enrichment/leads/${leadId}`)
}

export async function getEnrichmentMetrics(
  scope: "shipper" | "broker" = "shipper",
): Promise<EnrichmentMetrics> {
  const res = await api.GET("/enrichment/metrics", { params: { query: { kind: scope } } })
  return unwrap(res, "/enrichment/metrics")
}

export async function runAutoSend(dryRun = false): Promise<AutoSendResult> {
  const res = await api.POST("/enrichment/auto-send", { body: { dry_run: dryRun, now_hour_override: null } })
  return unwrap(res, "/enrichment/auto-send")
}
