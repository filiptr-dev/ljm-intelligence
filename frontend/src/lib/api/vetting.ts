/**
 * Vetting domain — thin wrapper over the typed openapi-fetch client.
 *
 * One route today: `GET /vetting/{key}`. The server normalises MC/DOT
 * strings; callers pass through user input verbatim.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type VetReport = components["schemas"]["VetReportOut"]
export type VetVerdict = VetReport["verdict"]
export type RedFlag = components["schemas"]["RedFlagOut"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function vetBroker(key: string): Promise<VetReport> {
  const res = await api.GET("/vetting/{key}", { params: { path: { key } } })
  return unwrap(res, `/vetting/${key}`)
}
