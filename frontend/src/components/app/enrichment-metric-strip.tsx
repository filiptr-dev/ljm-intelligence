"use client"

/**
 * EnrichmentMetricStrip — the four-number strip at the top of /shippers.
 *
 * `enriched · reachable · contacted · replied` + the two conversion rates,
 * scoped to shipper (default) or broker via `scope` prop. Reads once on mount,
 * refreshes on the parent's request via `refreshKey`.
 */

import { useEffect, useState } from "react"
import { getEnrichmentMetrics, type EnrichmentMetrics } from "@/lib/api/enrichment"

export function EnrichmentMetricStrip({
  scope = "shipper",
  refreshKey,
}: {
  scope?: "shipper" | "broker"
  refreshKey?: number
}) {
  const [m, setM] = useState<EnrichmentMetrics | null>(null)
  useEffect(() => {
    getEnrichmentMetrics(scope)
      .then(setM)
      .catch(() => setM(null))
  }, [scope, refreshKey])

  if (!m) return null
  const pct = (v: number) => `${Math.round(v * 100)}%`
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 rounded-sm border border-border bg-muted/40 p-2 text-xs sm:grid-cols-6">
      <Item label={`Enriched (${m.window_days}d)`} value={m.enriched} sub={`all-time ${m.enriched_all_time}`} />
      <Item label="Reachable" value={m.reachable} sub={`all-time ${m.reachable_all_time}`} />
      <Item label="Contacted" value={m.contacted} />
      <Item label="Replied" value={m.replied} />
      <Item label="Reachable rate" value={pct(m.reachable_rate)} />
      <Item label="Replied rate" value={pct(m.replied_rate)} />
    </div>
  )
}

function Item({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="font-semibold">{value}</div>
      {sub ? <div className="text-[10px] text-muted-foreground">{sub}</div> : null}
    </div>
  )
}
