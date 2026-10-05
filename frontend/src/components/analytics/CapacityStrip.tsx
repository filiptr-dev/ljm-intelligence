"use client"

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { FunnelChart, KpiTile } from "@/components/charts/primitives"
import * as analysis from "@/lib/api/analysis"

/**
 * Capacity analytics strip — three tiles + a tiny posted→matched→closed
 * funnel. Reads ``/analysis/capacity`` on mount; renders nothing on error so
 * it never blocks the page's existing content.
 */
export function CapacityStrip() {
  const [data, setData] = React.useState<analysis.CapacityKpi | null>(null)

  React.useEffect(() => {
    let cancelled = false
    analysis
      .getCapacityKpi({ period: "30d" })
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setData(null))
    return () => {
      cancelled = true
    }
  }, [])

  if (!data) return null
  return (
    <div className="mb-5 grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <div className="grid gap-3 sm:grid-cols-3">
        {data.tiles.map((t, i) => (
          <KpiTile key={t.label + i} block={t} showSparkline={false} />
        ))}
      </div>
      <Panel title="Posted → matched → closed" description="Last 30 days.">
        <FunnelChart funnel={data.funnel} />
      </Panel>
    </div>
  )
}
