"use client"

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { Breakdown } from "@/components/charts/primitives"
import { FilterBar } from "./FilterBar"
import { useSearchParams } from "next/navigation"
import * as analysis from "@/lib/api/analysis"
import type { PeriodLabel } from "@/lib/api/analysis"

const PERIOD_SET: ReadonlySet<PeriodLabel> = new Set(["today", "7d", "30d", "90d", "custom"])

/**
 * Lane performance strip for /loads — top lanes by load count, average rate,
 * average $/mi. Region is a *filter* (plan rule: never mix USD and EUR
 * averages in one chart) wired through ``FilterBar(showRegion)``.
 */
export function LaneStrip() {
  const sp = useSearchParams()
  const periodRaw = sp.get("period")
  const period: PeriodLabel = periodRaw && PERIOD_SET.has(periodRaw as PeriodLabel) ? (periodRaw as PeriodLabel) : "90d"
  const region = sp.get("region") ?? undefined
  const [data, setData] = React.useState<analysis.LaneKpi | null>(null)

  React.useEffect(() => {
    let cancelled = false
    analysis
      .getLaneKpi({ period, region })
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setData(null))
    return () => {
      cancelled = true
    }
  }, [period, region])

  return (
    <div className="mb-5 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <FilterBar showRegion />
        <span className="text-xs text-muted-foreground">Loads board, by lane</span>
      </div>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Top lanes" description="Most-posted lanes in this window.">
          {data ? (
            <Breakdown
              data={data.top}
              valueFormat={(v) => `${v.toLocaleString("en-US")} loads`}
            />
          ) : (
            <div className="h-32 animate-pulse rounded-sm bg-muted/40" />
          )}
        </Panel>
        <Panel title="Rate signal" description="Average rate + $/mi, by lane. Thin rows (n<5) marked.">
          {data ? (
            <ul className="divide-y divide-border text-sm">
              {data.rows.slice(0, 8).map((r) => (
                <li key={r.lane} className="flex items-center justify-between py-1.5">
                  <span className="font-mono text-xs">{r.lane}</span>
                  <span className="flex gap-3 text-right">
                    <span className="tabular-nums">
                      {r.avg_rate_usd !== null ? `$${Math.round(r.avg_rate_usd).toLocaleString("en-US")}` : "—"}
                    </span>
                    <span className="w-16 tabular-nums text-muted-foreground">
                      {r.avg_usd_per_mile !== null ? `$${r.avg_usd_per_mile.toFixed(2)}/mi` : "—"}
                    </span>
                    <span className="w-14 text-right text-xs text-muted-foreground">
                      n={r.n}
                      {r.n < 5 ? " ·thin" : ""}
                    </span>
                  </span>
                </li>
              ))}
              {!data.rows.length ? <li className="py-6 text-center text-muted-foreground">No loads in this window.</li> : null}
            </ul>
          ) : (
            <div className="h-32 animate-pulse rounded-sm bg-muted/40" />
          )}
        </Panel>
      </div>
    </div>
  )
}
