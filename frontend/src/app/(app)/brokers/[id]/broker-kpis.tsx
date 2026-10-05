"use client"

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { KpiTile } from "@/components/charts/primitives"
import * as analysis from "@/lib/api/analysis"

/**
 * Per-broker KPI block — swaps the hardcoded 30d summary for a period-aware
 * block fed by ``/analysis/broker-kpis/{id}``.
 *
 * Keeps the yellow placeholder below ("Email analytics — sample data") owned
 * by the inbox-connector plan; this block is just the honest sent/replied
 * numbers with proper period compare.
 */
export function BrokerKpis({ brokerId }: { brokerId: string }) {
  const [data, setData] = React.useState<analysis.BrokerKpi | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [period, setPeriod] = React.useState<analysis.PeriodLabel>("30d")

  React.useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const d = await analysis.getBrokerKpi(brokerId, { period })
        if (!cancelled) {
          setData(d)
          setLoading(false)
        }
      } catch {
        if (!cancelled) {
          setData(null)
          setLoading(false)
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [brokerId, period])

  return (
    <Panel
      title="Outreach KPIs"
      description="Sent, replied and reply-rate for the selected window — real data from sent_log."
      action={
        <div
          className="inline-flex overflow-hidden rounded-sm border border-border bg-background"
          role="radiogroup"
          aria-label="KPI period"
        >
          {(["7d", "30d", "90d"] as const).map((p) => (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={period === p}
              onClick={() => setPeriod(p)}
              className={
                period === p
                  ? "bg-asphalt px-2 py-0.5 text-[0.68rem] font-medium text-white"
                  : "px-2 py-0.5 text-[0.68rem] font-medium hover:bg-muted"
              }
            >
              {p}
            </button>
          ))}
        </div>
      }
    >
      {loading ? (
        <div className="h-20 animate-pulse rounded-sm bg-muted/40" aria-hidden />
      ) : !data ? (
        <p className="text-sm text-muted-foreground">Couldn&apos;t load KPIs.</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-3">
          {data.tiles.map((t, i) => (
            <KpiTile key={t.label + i} block={t} upIsGood={true} showSparkline={i === 0} />
          ))}
        </div>
      )}
    </Panel>
  )
}
