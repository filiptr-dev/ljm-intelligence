/**
 * Shipper Finder — /shippers. SERVER COMPONENT shell.
 *
 * Hands the first ranked page of shipper candidates to the client island
 * (`./shippers-client.tsx`) via SSR, so the first paint is real rows
 * instead of an "Initial load…" placeholder. The island keeps everything
 * interactive — filter chips, infinite scroll, the 6-second undo window
 * on "Add to leads" promotes, right-column detail fetches.
 *
 * One choice worth naming: we pre-fetch the *default* filter set only
 * (lane sort, no state/source filter, no search). As soon as the island
 * mounts, its filters-effect debounces for 200 ms and re-runs the same
 * query shape. Any user filter change triggers a fresh fetch client-side.
 * Pre-rendering only the default keeps this shell cheap and route-cacheable
 * if we ever want to; filtered pages don't carry a cold-start win.
 */

import { api } from "@/lib/api/server"
import ShippersClient, { type InitialShippersPage } from "./shippers-client"
import type { components } from "@/lib/api/types"

const PAGE_LIMIT = 50

export const dynamic = "force-dynamic"

export default async function ShippersPage() {
  let initial: InitialShippersPage = null
  try {
    const { data, response } = await api.GET("/tools/shipper-finder", {
      params: { query: { limit: PAGE_LIMIT, sort: "lane" } },
    })
    if (response.ok && data) {
      const d = data as components["schemas"]["ShipperListOut"]
      initial = { items: d.items, next_cursor: d.next_cursor ?? null }
    }
  } catch {
    initial = null
  }
  return <ShippersClient initial={initial} />
}
