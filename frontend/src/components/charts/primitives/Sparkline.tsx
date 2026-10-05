"use client"

import { Line, LineChart, ResponsiveContainer } from "recharts"
import type { MetricPoint } from "@/lib/api/analysis"

/**
 * Tiny inline sparkline — the ``series`` slot on a ``KpiTile``.
 *
 * No axes, no tooltip; it's a shape, not a chart. recharts' Responsive
 * container keeps it crisp at the tile widths we actually use.
 */
export function Sparkline({ series, className = "h-10 w-24" }: { series: MetricPoint[]; className?: string }) {
  if (!series.length) return <div className={className} aria-hidden />
  const data = series.map((p) => ({ v: p.value }))
  return (
    <div className={className}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
          <Line
            type="monotone"
            dataKey="v"
            stroke="var(--chart-1)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
