/**
 * Fleet (Kamioni) — typed wrappers over the generated `schema.d.ts`.
 *
 * Same single openapi-fetch client as everything else (server: direct + bearer
 * from the session cookie; browser: `/api/proxy`). No ad-hoc fetch.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type FleetList = components["schemas"]["FleetList"]
export type TruckRow = components["schemas"]["TruckRow"]
export type TruckDetail = components["schemas"]["TruckDetail"]
export type AlertStrip = components["schemas"]["AlertStrip"]
export type DocRef = components["schemas"]["DocRef"]
export type DefectRef = components["schemas"]["DefectRef"]
export type MaintRef = components["schemas"]["MaintRef"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function getTrucks(signal?: AbortSignal): Promise<FleetList> {
  return unwrap(await api.GET("/fleet/trucks", { signal }), "/fleet/trucks")
}

export async function getAlerts(signal?: AbortSignal): Promise<AlertStrip> {
  return unwrap(await api.GET("/fleet/alerts", { signal }), "/fleet/alerts")
}

export async function getTruckDetail(id: number, signal?: AbortSignal): Promise<TruckDetail> {
  return unwrap(await api.GET("/fleet/trucks/{unit_id}", { params: { path: { unit_id: id } }, signal }), "/fleet/trucks/{id}")
}
