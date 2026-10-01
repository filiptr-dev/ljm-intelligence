/**
 * AI provider layer — typed client for /ai/features and /ai/usage.
 *
 * Mirrors the `enrichment.ts` + `shipper-finder.ts` pattern: every call goes
 * through the shared openapi-fetch client. Types regenerated via `pnpm gen:api`
 * — never hand-type a response shape.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type FeatureChoice = components["schemas"]["FeatureChoice"]
export type FeaturesResponse = components["schemas"]["FeaturesResponse"]
export type UsageRow = components["schemas"]["UsageRow"]
export type UsageTotal = components["schemas"]["UsageTotal"]
export type UsageResponse = components["schemas"]["UsageResponse"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function getFeatures(): Promise<FeaturesResponse> {
  const res = await api.GET("/ai/features", {})
  return unwrap(res, "/ai/features")
}

export async function getUsage(params: {
  since?: string
  feature?: string
  provider?: string
  limit?: number
} = {}): Promise<UsageResponse> {
  const query: Record<string, string | number> = {}
  if (params.since) query.since = params.since
  if (params.feature) query.feature = params.feature
  if (params.provider) query.provider = params.provider
  if (params.limit) query.limit = params.limit
  const res = await api.GET("/ai/usage", { params: { query } })
  return unwrap(res, "/ai/usage")
}
