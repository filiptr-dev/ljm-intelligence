"use client"

import { Bar, BarChart, CartesianGrid, LabelList, Line, LineChart, XAxis, YAxis } from "recharts"
import {
  ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import { monthLabel } from "@/lib/format"

const grid = <CartesianGrid vertical={false} stroke="var(--grid)" />
const axisProps = { tickLine: false, axisLine: false, tickMargin: 8 } as const

type Month = { month: string; booked: number; rejected: number; decided: number; winRate: number; usdPerMile: number | null; eurPerKm: number | null }

const outcomeConfig = {
  booked: { label: "Booked", color: "var(--chart-1)" },
  rejected: { label: "Rejected by broker", color: "var(--chart-3)" },
} satisfies ChartConfig

export function OutcomeColumns({ data, className = "h-64 w-full" }: { data: Month[]; className?: string }) {
  return (
    <ChartContainer config={outcomeConfig} className={className}>
      <BarChart data={data} margin={{ left: -18, right: 4, top: 8 }} barCategoryGap="22%">
        {grid}
        <XAxis dataKey="month" tickFormatter={monthLabel} {...axisProps} minTickGap={12} />
        <YAxis {...axisProps} allowDecimals={false} />
        <ChartTooltip cursor={{ fill: "var(--muted)" }} content={<ChartTooltipContent labelFormatter={(l) => monthLabel(String(l))} />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="booked" stackId="a" fill="var(--color-booked)" maxBarSize={24} stroke="var(--card)" strokeWidth={1} />
        <Bar dataKey="rejected" stackId="a" fill="var(--color-rejected)" radius={[4, 4, 0, 0]} maxBarSize={24} stroke="var(--card)" strokeWidth={1} />
      </BarChart>
    </ChartContainer>
  )
}

export function WinRateLine({ data, className = "h-48 w-full" }: { data: { month: string; winRate: number; decided: number }[]; className?: string }) {
  // months with too few decided loads are noise, not signal
  const rows = data.filter((d) => d.decided >= 8).map((d) => ({ ...d, pct: Math.round(d.winRate * 100) }))
  const last = rows[rows.length - 1]
  return (
    <ChartContainer config={{ pct: { label: "Win rate", color: "var(--chart-1)" } }} className={className}>
      <LineChart data={rows} margin={{ left: -18, right: 30, top: 12 }}>
        {grid}
        <XAxis dataKey="month" tickFormatter={monthLabel} {...axisProps} minTickGap={16} />
        <YAxis {...axisProps} domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(l) => monthLabel(String(l))} formatter={(v) => <span className="font-mono">Win rate {v}%</span>} />} />
        <Line dataKey="pct" stroke="var(--color-pct)" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}>
          <LabelList
            dataKey="pct"
            content={({ x, y, index }) =>
              index === rows.length - 1 ? (
                <text x={Number(x) + 6} y={Number(y) + 4} className="fill-foreground text-[11px] font-semibold">{last.pct}%</text>
              ) : null
            }
          />
        </Line>
      </LineChart>
    </ChartContainer>
  )
}

export function RateLine({
  data, dataKey, label, unit, className = "h-44 w-full",
}: { data: Month[]; dataKey: "usdPerMile" | "eurPerKm"; label: string; unit: string; className?: string }) {
  const rows = data.filter((d) => d[dataKey] !== null)
  const vals = rows.map((d) => d[dataKey] as number)
  const min = Math.floor((Math.min(...vals) - 0.1) * 10) / 10
  const max = Math.ceil((Math.max(...vals) + 0.1) * 10) / 10
  return (
    <ChartContainer config={{ [dataKey]: { label, color: "var(--chart-1)" } }} className={className}>
      <LineChart data={rows} margin={{ left: -8, right: 8, top: 8 }}>
        {grid}
        <XAxis dataKey="month" tickFormatter={monthLabel} {...axisProps} minTickGap={18} />
        <YAxis {...axisProps} domain={[min, max]} tickFormatter={(v) => `${unit}${Number(v).toFixed(2)}`} width={52} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(l) => monthLabel(String(l))} />} />
        <Line dataKey={dataKey} stroke={`var(--color-${dataKey})`} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }} />
      </LineChart>
    </ChartContainer>
  )
}

export function SpeedColumns({ data, className = "h-56 w-full" }: { data: { label: string; winRate: number; quotes: number }[]; className?: string }) {
  const rows = data.map((d) => ({ ...d, pct: Math.round(d.winRate * 100) }))
  return (
    <ChartContainer config={{ pct: { label: "Win rate", color: "var(--chart-1)" } }} className={className}>
      <BarChart data={rows} margin={{ left: -18, right: 4, top: 22 }}>
        {grid}
        <XAxis dataKey="label" {...axisProps} />
        <YAxis {...axisProps} domain={[0, 70]} tickFormatter={(v) => `${v}%`} />
        <ChartTooltip
          cursor={{ fill: "var(--muted)" }}
          content={<ChartTooltipContent hideIndicator formatter={(v, _n, item) => <span className="font-mono">Win rate {v}% · {item.payload.quotes} quotes</span>} />}
        />
        <Bar dataKey="pct" fill="var(--color-pct)" radius={[4, 4, 0, 0]} maxBarSize={24}>
          <LabelList dataKey="pct" position="top" formatter={(v) => `${v}%`} className="fill-foreground text-[12px] font-semibold" />
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}

export function ActivityColumns({ data, className = "h-48 w-full" }: { data: { month: string; booked: number; rejected: number }[]; className?: string }) {
  return (
    <ChartContainer config={outcomeConfig} className={className}>
      <BarChart data={data} margin={{ left: -24, right: 4, top: 8 }}>
        {grid}
        <XAxis dataKey="month" tickFormatter={monthLabel} {...axisProps} minTickGap={10} />
        <YAxis {...axisProps} allowDecimals={false} />
        <ChartTooltip cursor={{ fill: "var(--muted)" }} content={<ChartTooltipContent labelFormatter={(l) => monthLabel(String(l))} />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="booked" stackId="a" fill="var(--color-booked)" maxBarSize={20} stroke="var(--card)" strokeWidth={1} />
        <Bar dataKey="rejected" stackId="a" fill="var(--color-rejected)" radius={[4, 4, 0, 0]} maxBarSize={20} stroke="var(--card)" strokeWidth={1} />
      </BarChart>
    </ChartContainer>
  )
}
