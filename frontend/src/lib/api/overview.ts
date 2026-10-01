/**
 * Overview — the operator's Today desk.
 *
 * One function, one shape: `getToday()` → `OverviewTodayOut`. The server owns
 * the ordering of `do_next[]` and the demo flags on the two tiles — the UI
 * never re-sorts or re-filters.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type OverviewToday = components["schemas"]["OverviewTodayOut"]
export type DoNextRow = OverviewToday["do_next"][number]
export type DoNextCall = components["schemas"]["DoNextCall"]
export type DoNextCapacityMatch = components["schemas"]["DoNextCapacityMatch"]
export type DoNextNewLead = components["schemas"]["DoNextNewLead"]

/** GET /overview/today — single aggregate read for the login landing page. */
export async function getToday(signal?: AbortSignal): Promise<OverviewToday> {
  const { data, response } = await api.GET("/overview/today", { signal })
  if (!data) throw new ApiRequestError(response.status, "/overview/today")
  return data
}
