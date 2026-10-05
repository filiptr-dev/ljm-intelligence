"use client"

import {
  CartesianGrid,
  Line,
  LineChart,
  XAxis,
  YAxis,
} from "recharts"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import type { MetricPoint } from "@/lib/api/analysis"
import { EmptyChart } from "./EmptyChart"
import { ThinSample } from "./ThinSample"

/**
 * A single-series line trend. Axes carry a unit; a thin-sample strip renders
 * below when any point's ``n`` is below ``minN``.
 *
 * One consistent config: ``--chart-1`` colour, dashed line when thin.
 */
export function TrendLine({
  series,
  label,
  unit = "",
  minN = 10,
  className = "h-48 w-full",
  emptyTitle = "No data in this window",
}: {
  series: MetricPoint[]
  label: string
  unit?: string
  minN?: number
  className?: string
  emptyTitle?: string
}) {
  if (series.length === 0) {
    return <EmptyChart title={emptyTitle} />
  }
  const thin = series.some((p) => p.n < minN)
  const minN_val = series.reduce((m, p) => Math.min(m, p.n), series[0].n)
  const data = series.map((p) => ({ key: p.key, value: p.value, n: p.n }))
  const config = { value: { label, color: "var(--chart-1)" } } satisfies ChartConfig
  return (
    <div className="space-y-2">
      <ChartContainer config={config} className={className}>
        <LineChart data={data} margin={{ left: -8, right: 12, top: 10 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="key" tickLine={false} axisLine={false} tickMargin={8} minTickGap={16} />
          <YAxis
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            tickFormatter={(v) => `${unit}${Number(v).toLocaleString("en-US")}`}
            width={60}
            label={unit ? { value: label, angle: -90, position: "insideLeft", offset: 20, style: { fill: "var(--muted-foreground)", fontSize: 11 } } : undefined}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                formatter={(v, _n, item) => (
                  <span className="font-mono">
                    {label}: {unit}
                    {Number(v).toLocaleString("en-US")}
                    <span className="ml-2 text-muted-foreground">
                      (n={(item?.payload as { n?: number } | undefined)?.n ?? 0})
                    </span>
                  </span>
                )}
              />
            }
          />
          <Line
            dataKey="value"
            stroke="var(--color-value)"
            strokeWidth={2}
            strokeDasharray={thin ? "4 3" : undefined}
            dot={false}
            activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}
          />
        </LineChart>
      </ChartContainer>
      {thin ? <ThinSample n={minN_val} min={minN} /> : null}
    </div>
  )
}
