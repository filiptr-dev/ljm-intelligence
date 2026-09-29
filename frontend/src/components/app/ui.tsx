import * as React from "react"
import { ArrowDownRight, ArrowUpRight } from "lucide-react"
import type { Segment } from "@/lib/analytics"
import type { Region } from "@/lib/data/geo"
import { cn } from "@/lib/utils"

export function PageHeader({
  eyebrow, title, description, actions, art,
}: { eyebrow: string; title: string; description?: React.ReactNode; actions?: React.ReactNode; art?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end">
      <div className="min-w-0 flex-1">
        <div className="eyebrow mb-1.5 flex items-center gap-2">
          <span className="inline-block h-3 w-1 bg-safety" aria-hidden />
          {eyebrow}
        </div>
        <h1 className="font-display text-3xl leading-none font-bold md:text-[2.5rem]">{title}</h1>
        {description ? <p className="mt-2 max-w-2xl text-[0.95rem] text-muted-foreground">{description}</p> : null}
      </div>
      {art ? <div className="hidden w-64 shrink-0 xl:block">{art}</div> : null}
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export function Panel({
  title, description, action, children, className, bodyClassName, id,
}: {
  title: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
  className?: string
  bodyClassName?: string
  id?: string
}) {
  return (
    <section id={id} className={cn("flex min-w-0 scroll-mt-20 flex-col rounded-sm border border-border bg-card", className)}>
      <header className="flex items-start gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-[1.05rem] leading-tight font-semibold">{title}</h2>
          {description ? <p className="mt-0.5 text-[0.8rem] text-muted-foreground">{description}</p> : null}
        </div>
        {action}
      </header>
      <div className={cn("min-w-0 flex-1 p-4", bodyClassName)}>{children}</div>
    </section>
  )
}

export function StatTile({
  label, value, delta, deltaLabel, upIsGood = true, sub, icon,
}: {
  label: string
  value: React.ReactNode
  delta?: number
  deltaLabel?: string
  upIsGood?: boolean
  sub?: React.ReactNode
  icon?: React.ReactNode
}) {
  const good = delta === undefined ? undefined : delta >= 0 === upIsGood
  return (
    <div className="relative min-w-0 overflow-hidden rounded-sm border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-[0.8rem] text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 text-[1.9rem] leading-none font-semibold tracking-tight">{value}</div>
      {delta !== undefined ? (
        <div className={cn("mt-2 flex items-center gap-1 text-xs font-medium", good ? "text-good" : "text-bad")}>
          {delta >= 0 ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
          <span>{deltaLabel}</span>
        </div>
      ) : sub ? (
        <div className="mt-2 text-xs text-muted-foreground">{sub}</div>
      ) : null}
    </div>
  )
}

export const SEGMENT_COLOR: Record<Segment, string> = {
  "Core partners": "var(--chart-1)",
  Growing: "var(--chart-5)",
  "Price shoppers": "var(--chart-2)",
  Dormant: "var(--chart-4)",
  Occasional: "var(--steel)",
}

export function SegmentBadge({ segment, className }: { segment: Segment; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-sm border border-border bg-background px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", className)}>
      <span className="size-2 rounded-[2px]" style={{ background: SEGMENT_COLOR[segment] }} aria-hidden />
      {segment}
    </span>
  )
}

export function RegionTag({ region }: { region: Region }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-[3px] px-1.5 font-mono text-[0.65rem] font-semibold",
        region === "US" ? "bg-asphalt text-white" : "bg-[#1f4aa8] text-white",
      )}
    >
      {region}
    </span>
  )
}

/** Horizontal bar list: one series, one color, value at the bar tip. */
export function BarList({
  rows, color = "var(--chart-1)", format = (v: number) => String(v), max,
}: {
  rows: { label: React.ReactNode; value: number; hint?: string; href?: string; sub?: React.ReactNode }[]
  color?: string
  format?: (v: number) => string
  max?: number
}) {
  const top = max ?? Math.max(...rows.map((r) => r.value), 1)
  return (
    <ul className="space-y-2.5">
      {rows.map((r, i) => (
        <li key={i} className="group" title={r.hint}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate">{r.label}</span>
            <span className="num shrink-0 font-mono text-[0.8rem] font-medium">{format(r.value)}</span>
          </div>
          <div className="h-2.5 w-full bg-muted">
            <div
              className="h-full rounded-r-[3px] transition-opacity group-hover:opacity-80"
              style={{ width: `${Math.max(2, (r.value / top) * 100)}%`, background: color }}
            />
          </div>
          {r.sub ? <div className="mt-1 text-xs text-muted-foreground">{r.sub}</div> : null}
        </li>
      ))}
    </ul>
  )
}

export function Sparkline({ values, className, color = "var(--chart-1)" }: { values: number[]; className?: string; color?: string }) {
  const w = 84
  const h = 22
  const max = Math.max(...values, 1)
  const step = w / (values.length - 1)
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v / max) * (h - 4)).toFixed(1)}`)
  const last = pts[pts.length - 1].split(",")
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={cn("h-[22px] w-[84px]", className)} aria-hidden>
      <polyline points={pts.join(" ")} fill="none" stroke="var(--steel)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill={color} stroke="#fff" strokeWidth="1" />
    </svg>
  )
}

/** Single 100% stacked bar with 2px surface gaps between segments. */
export function StackBar({ parts }: { parts: { label: string; value: number; color: string }[] }) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1
  return (
    <div>
      <div className="flex h-7 w-full gap-[2px]">
        {parts.map((p) => (
          <div
            key={p.label}
            title={`${p.label}: ${p.value}`}
            className="h-full first:rounded-l-[3px] last:rounded-r-[3px]"
            style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
          />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-[2px]" style={{ background: p.color }} aria-hidden />
            <span>{p.label}</span>
            <span className="num font-mono text-xs text-muted-foreground">{p.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function HealthPill({ value, delta }: { value: number; delta?: number }) {
  const tone = value >= 70 ? "bg-good" : value >= 45 ? "bg-chart-2" : "bg-bad"
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-1.5 w-10 bg-muted" aria-hidden>
        <span className={cn("block h-full", tone)} style={{ width: `${value}%` }} />
      </span>
      <span className="num font-mono text-xs font-semibold">{value}</span>
      {delta !== undefined && Math.abs(delta) >= 3 ? (
        <span className={cn("text-[0.7rem] font-medium", delta > 0 ? "text-good" : "text-bad")}>
          {delta > 0 ? "▲" : "▼"}
          {Math.abs(delta)}
        </span>
      ) : null}
    </span>
  )
}
