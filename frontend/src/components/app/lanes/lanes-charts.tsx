"use client"

import { Bar, BarChart, CartesianGrid, ComposedChart, LabelList, Line, XAxis, YAxis } from "recharts"
import {
  ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import { EmptyChart } from "@/components/charts/primitives"
import type { LanesSummary } from "@/lib/api/lanes"
import { usd } from "./format"

const grid = <CartesianGrid vertical={false} stroke="var(--grid)" />
const axis = { tickLine: false, axisLine: false, tickMargin: 8 } as const

const costConfig = {
  fuel: { label: "Fuel", color: "var(--chart-1)" },
  driver: { label: "Driver pay", color: "var(--chart-2)" },
  load: { label: "Load costs", color: "var(--chart-3)" },
  dispatch: { label: "Dispatch", color: "var(--chart-4)" },
  revenue: { label: "Revenue", color: "var(--chart-5)" },
} satisfies ChartConfig

/** Stacked cost per bucket (fuel / driver / load / dispatch) with a revenue line on top. */
export function CostStackChart({ buckets }: { buckets: LanesSummary["buckets"] }) {
  if (!buckets.length) return <EmptyChart title="No runs in this window" hint="Pick a longer period or clear the filter." />
  const data = buckets.map((b) => ({
    label: b.label,
    fuel: b.metrics.cost_fuel,
    driver: b.metrics.cost_driver,
    load: b.metrics.cost_load,
    dispatch: b.metrics.cost_dispatch,
    revenue: b.metrics.revenue,
  }))
  return (
    <ChartContainer config={costConfig} className="h-72 w-full">
      <ComposedChart data={data} margin={{ left: 0, right: 8, top: 8 }} barCategoryGap="18%">
        {grid}
        <XAxis dataKey="label" {...axis} minTickGap={14} />
        <YAxis {...axis} width={52} tickFormatter={(v) => usd(Number(v))} />
        <ChartTooltip
          cursor={{ fill: "var(--muted)" }}
          content={<ChartTooltipContent formatter={(v, name) => (
            <span className="flex w-full justify-between gap-4">
              <span className="text-muted-foreground">{costConfig[name as keyof typeof costConfig]?.label ?? name}</span>
              <span className="num font-mono font-medium">{usd(Number(v))}</span>
            </span>
          )} />}
        />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="fuel" stackId="c" fill="var(--color-fuel)" maxBarSize={36} />
        <Bar dataKey="driver" stackId="c" fill="var(--color-driver)" maxBarSize={36} />
        <Bar dataKey="load" stackId="c" fill="var(--color-load)" maxBarSize={36} />
        <Bar dataKey="dispatch" stackId="c" fill="var(--color-dispatch)" radius={[3, 3, 0, 0]} maxBarSize={36} />
        <Line dataKey="revenue" type="monotone" stroke="var(--color-revenue)" strokeWidth={2} dot={{ r: 2.5, fill: "var(--color-revenue)" }} />
      </ComposedChart>
    </ChartContainer>
  )
}

const bandConfig = { runs: { label: "Runs", color: "var(--chart-1)" } } satisfies ChartConfig

export function LengthBandChart({ bands }: { bands: LanesSummary["length_bands"] }) {
  if (!bands.some((b) => b.runs)) return <EmptyChart title="No runs in this window" />
  return (
    <ChartContainer config={bandConfig} className="h-56 w-full">
      <BarChart data={bands} margin={{ left: 0, right: 8, top: 18 }} barCategoryGap="22%">
        {grid}
        <XAxis dataKey="band" {...axis} />
        <YAxis {...axis} width={36} allowDecimals={false} />
        <ChartTooltip cursor={{ fill: "var(--muted)" }} content={<ChartTooltipContent />} />
        <Bar dataKey="runs" fill="var(--color-runs)" radius={[3, 3, 0, 0]} maxBarSize={44}>
          <LabelList dataKey="pct" position="top" className="fill-muted-foreground text-[11px]" formatter={(v) => `${Number(v).toFixed(0)}%`} />
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}
