/**
 * Shipper Finder domain module — thin wrappers over the typed client.
 *
 * One module per domain (bb-unikoop pattern). All types come from the generated
 * `schema.d.ts` — no `any`, no hand-typed shapes; if the API changes, `pnpm
 * gen:api` regenerates and `tsc` fails until the callers catch up.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type ShipperRow = components["schemas"]["ShipperRowOut"]
export type ShipperList = components["schemas"]["ShipperListOut"]
export type ShipperDetail = components["schemas"]["ShipperDetailOut"]
export type PromoteResult = components["schemas"]["PromoteOut"]
export type PromotedLead = components["schemas"]["PromotedLeadOut"]

export type ShipperSource = "FMCSA" | "OSM" | "Both"

export type ShipperSort = "lane" | "fit"

export type ListShippersQuery = {
  state?: string
  source?: ShipperSource
  min_score?: number
  promoted?: boolean
  q?: string
  cursor?: string
  limit?: number
  // Sort control. "lane" = existing lane-ranking (default); "fit" = fit_score
  // DESC, unscored last. The backend does the sort so cursor pagination stays
  // correct across pages.
  sort?: ShipperSort
}

/** GET /tools/shipper-finder — cursor-paginated ranked list. */
export async function listShippers(
  query: ListShippersQuery = {},
  signal?: AbortSignal,
): Promise<ShipperList> {
  const { data, response } = await api.GET("/tools/shipper-finder", {
    params: { query },
    signal,
  })
  if (!data) throw new ApiRequestError(response.status, "/tools/shipper-finder")
  return data
}

/** GET /tools/shipper-finder/{id} — evidence + optional promoted lead. */
export async function getShipper(
  candidate_id: string,
  signal?: AbortSignal,
): Promise<ShipperDetail> {
  const { data, response } = await api.GET("/tools/shipper-finder/{candidate_id}", {
    params: { path: { candidate_id } },
    signal,
  })
  if (!data) throw new ApiRequestError(response.status, "/tools/shipper-finder/{candidate_id}")
  return data
}

/** POST /tools/shipper-finder/promote — idempotent one-way write into `leads`. */
export async function promoteShipper(
  candidate_id: string,
  signal?: AbortSignal,
): Promise<PromoteResult> {
  const { data, response } = await api.POST("/tools/shipper-finder/promote", {
    body: { candidate_id },
    signal,
  })
  if (!data) throw new ApiRequestError(response.status, "/tools/shipper-finder/promote")
  return data
}
