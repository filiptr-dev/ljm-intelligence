import * as React from "react"
import type { Funnel } from "@/lib/api/analysis"
import { cn } from "@/lib/utils"
import { EmptyChart } from "./EmptyChart"

/**
 * One-column funnel with per-step counts and drop percentages.
 *
 * Not a chart library's funnel — the shape is simple and we want full control
 * over "drop_pct" labels and dark-mode colours via CSS vars.
 */
export function FunnelChart({ funnel, className }: { funnel: Funnel; className?: string }) {
  if (!funnel.steps.length) return <EmptyChart title="No funnel data yet" />
  const top = funnel.steps[0]?.count ?? 0
  if (top === 0) return <EmptyChart title="No funnel data in this window" />
  return (
    <ol className={cn("space-y-2", className)}>
      {funnel.steps.map((s, i) => {
        const share = top ? s.count / top : 0
        const width = Math.max(10, share * 100)
        return (
          <li key={`${s.label}-${i}`}>
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">{s.label}</span>
              <span className="font-mono tabular-nums">
                {s.count.toLocaleString("en-US")}
                {s.drop_pct !== null && s.drop_pct !== undefined ? (
                  <span className="ml-2 text-xs text-muted-foreground">
                    −{s.drop_pct.toFixed(0)}% drop
                  </span>
                ) : null}
              </span>
            </div>
            <div className="mt-1 h-3 rounded-sm bg-muted/40">
              <div
                className="h-3 rounded-sm"
                style={{ width: `${width}%`, background: `var(--chart-${(i % 5) + 1})` }}
                aria-hidden
              />
            </div>
          </li>
        )
      })}
    </ol>
  )
}
