import * as React from "react"
import type { Breakdown as BreakdownT } from "@/lib/api/analysis"
import { cn } from "@/lib/utils"
import { EmptyChart } from "./EmptyChart"

/**
 * Horizontal bar list with ``n`` + share, honest about thin rows.
 *
 * Replaces the ad-hoc `BarList` on analytics surfaces — every row carries
 * its own sample size, which is what makes a share percentage trustworthy.
 */
export function Breakdown({
  data,
  valueFormat,
  color = "var(--chart-1)",
  className,
  rowHref,
  emptyTitle = "No data in this window",
}: {
  data: BreakdownT
  valueFormat?: (v: number) => string
  color?: string
  className?: string
  rowHref?: (key: string) => string
  emptyTitle?: string
}) {
  const fmt = valueFormat ?? ((v: number) => v.toLocaleString("en-US"))
  if (!data.rows.length) {
    return <EmptyChart title={emptyTitle} />
  }
  const max = data.rows.reduce((m, r) => Math.max(m, r.value), 0) || 1
  return (
    <ul className={cn("space-y-1.5", className)}>
      {data.rows.map((r) => {
        const label = (
          <>
            <span className="truncate">{r.key}</span>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              n={r.n} · {Math.round(r.share * 100)}%
            </span>
            <span className="shrink-0 font-mono font-semibold tabular-nums">
              {fmt(r.value)}
            </span>
          </>
        )
        return (
          <li key={r.key}>
            {rowHref ? (
              <a href={rowHref(r.key)} className="block hover:bg-muted/40">
                <RowBar label={label} value={r.value} max={max} color={color} />
              </a>
            ) : (
              <RowBar label={label} value={r.value} max={max} color={color} />
            )}
          </li>
        )
      })}
    </ul>
  )
}

function RowBar({
  label,
  value,
  max,
  color,
}: {
  label: React.ReactNode
  value: number
  max: number
  color: string
}) {
  const pct = Math.max(2, (value / max) * 100)
  return (
    <div className="relative overflow-hidden rounded-sm bg-muted/30">
      <div
        className="absolute inset-y-0 left-0 opacity-30"
        style={{ width: `${pct}%`, background: color }}
        aria-hidden
      />
      <div className="relative flex items-center justify-between gap-3 px-2.5 py-1.5 text-sm">
        {label}
      </div>
    </div>
  )
}
