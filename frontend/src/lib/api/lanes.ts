/**
 * Lanes history — typed wrappers over the generated `schema.d.ts`.
 *
 * Every call goes through the one openapi-fetch client (server: direct +
 * bearer from the session cookie; browser: `/api/proxy`). No ad-hoc fetch.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type LanesPeriod = "week" | "month" | "year"
export type LanesSummary = components["schemas"]["LanesSummary"]
export type LaneMetrics = components["schemas"]["LaneMetrics"]
export type LaneTrend = components["schemas"]["LaneTrend"]
export type LaneStatement = components["schemas"]["LaneStatement"]
export type TopLanes = components["schemas"]["TopLanes"]
export type TopLane = components["schemas"]["TopLane"]
export type HeatmapData = components["schemas"]["HeatmapData"]
export type LaneEntity = components["schemas"]["LaneEntity"]
export type CityEntity = components["schemas"]["CityEntity"]
export type HeatArc = components["schemas"]["HeatArc"]
export type HeatPoint = components["schemas"]["HeatPoint"]
export type RunsPage = components["schemas"]["RunsPage"]
export type RunRow = components["schemas"]["RunRow"]
export type LaneAiInsights = components["schemas"]["LaneAiInsights"]

/** The two filters the page can carry. `lane` is `"Chicago,IL>Atlanta,GA"`. */
export type LanesFilter = { state?: string; lane?: string }

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

const q = (period: LanesPeriod, f: LanesFilter = {}) => ({ period, state: f.state, lane: f.lane })

export async function getLanesSummary(period: LanesPeriod, f: LanesFilter = {}, signal?: AbortSignal): Promise<LanesSummary> {
  return unwrap(await api.GET("/analysis/lanes/summary", { params: { query: q(period, f) }, signal }), "/analysis/lanes/summary")
}

export async function getTopLanes(
  period: LanesPeriod, level: "state" | "city", f: LanesFilter = {}, signal?: AbortSignal,
): Promise<TopLanes> {
  return unwrap(
    await api.GET("/analysis/lanes/top", { params: { query: { ...q(period, f), level, limit: 15 } }, signal }),
    "/analysis/lanes/top",
  )
}

export async function getLanesHeatmap(period: LanesPeriod, f: LanesFilter = {}, signal?: AbortSignal): Promise<HeatmapData> {
  return unwrap(await api.GET("/analysis/lanes/heatmap", { params: { query: q(period, f) }, signal }), "/analysis/lanes/heatmap")
}

export async function getLanesRuns(
  period: LanesPeriod, f: LanesFilter = {}, cursor?: string | null, signal?: AbortSignal,
): Promise<RunsPage> {
  return unwrap(
    await api.GET("/analysis/lanes/runs", { params: { query: { ...q(period, f), cursor: cursor ?? undefined, limit: 12 } }, signal }),
    "/analysis/lanes/runs",
  )
}

/** Cached per-entity suggestions only — never triggers a model call. */
export async function getCachedEntityInsights(
  period: LanesPeriod, f: LanesFilter = {}, signal?: AbortSignal,
): Promise<Record<string, string[]>> {
  return unwrap(await api.GET("/analysis/lanes/ai", { params: { query: q(period, f) }, signal }), "/analysis/lanes/ai")
}

export async function postLaneInsights(
  period: LanesPeriod, f: LanesFilter = {}, refresh = false, signal?: AbortSignal,
): Promise<LaneAiInsights> {
  return unwrap(
    await api.POST("/analysis/lanes/ai", { params: { query: { ...q(period, f), refresh } }, signal }),
    "/analysis/lanes/ai",
  )
}
