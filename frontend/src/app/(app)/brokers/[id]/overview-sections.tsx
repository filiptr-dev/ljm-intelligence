"use client"

/**
 * Broker detail — three sections restored from pre-ba15198:
 *
 *   1. Health gauge — single ring 0..100 + delta arrow + per-component bars.
 *   2. 6-tile stat row — Sent 30d, Replied 30d, Reply rate, Avg reply hrs,
 *      Booked 12m, Rejected 12m. Each tile shows a "thin" badge when the
 *      underlying count is below 5.
 *   3. 12-month activity chart — booked + rejected stacked bars + sent line.
 *
 * All three read from the same ``overview_metrics`` block the list page uses
 * (one aggregate, two surfaces). Sections hide when their data is null/thin.
 * The AI summary card remains deferred until GET /analysis/lead/{id} exists.
 */

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { cn } from "@/lib/utils"
import { pct } from "@/lib/format"
import type { OverviewMetrics } from "@/lib/api/brokers"

const COMPONENT_LABEL: Record<string, string> = {
  recency: "Recency",
  win_rate: "Win rate",
  tone: "Tone",
  volume: "Volume",
}

export function HealthGaugeCard({ om }: { om: OverviewMetrics }) {
  const tone = om.health_score >= 70 ? "stroke-good" : om.health_score >= 45 ? "stroke-chart-2" : "stroke-bad"
  const r = 42
  const circ = 2 * Math.PI * r
  const dash = circ * (om.health_score / 100)
  const d = om.health_delta
  return (
    <Panel
      title="Relationship health"
      description="Recency + win rate + tone + volume (30/30/20/20)."
    >
      <div className="grid gap-4 sm:grid-cols-[120px_minmax(0,1fr)] sm:items-center">
        <div className="relative mx-auto size-28">
          <svg viewBox="0 0 100 100" className="size-28 -rotate-90">
            <circle cx="50" cy="50" r={r} className="fill-none stroke-muted" strokeWidth="10" />
            <circle
              cx="50"
              cy="50"
              r={r}
              className={cn("fill-none", tone)}
              strokeWidth="10"
              strokeDasharray={`${dash} ${circ}`}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="font-mono text-2xl font-bold">{om.health_score}</span>
            {d !== null && d !== undefined && Math.abs(d) >= 3 ? (
              <span className={cn("text-xs font-semibold", d > 0 ? "text-good" : "text-bad")}>
                {d > 0 ? "▲" : "▼"} {Math.abs(d)}
              </span>
            ) : null}
          </div>
        </div>
        <ul className="space-y-1.5 text-xs">
          {Object.entries(om.health_components).map(([k, v]) => {
            const label = COMPONENT_LABEL[k] ?? k
            const w = Math.max(2, Math.round(v))
            return (
              <li key={k} className="grid grid-cols-[80px_minmax(0,1fr)_28px] items-center gap-2">
                <span className="text-muted-foreground">{label}</span>
                <span className="h-1.5 w-full rounded-sm bg-muted">
                  <span className="block h-full rounded-sm bg-asphalt" style={{ width: `${w}%` }} aria-hidden />
                </span>
                <span className="num text-right font-mono">{Math.round(v)}</span>
              </li>
            )
          })}
          {om.health_thin ? (
            <li className="text-[0.7rem] italic text-muted-foreground">
              Thin signal — fewer than 5 decided calls. The win-rate component contributes at half-weight.
            </li>
          ) : null}
        </ul>
      </div>
    </Panel>
  )
}

export function StatTilesRow({ om }: { om: OverviewMetrics }) {
  const tiles: Array<{ label: string; value: string; thin?: boolean }> = [
    { label: "Sent · 30d", value: String(om.sent_30d), thin: om.sent_30d < 5 },
    { label: "Replied · 30d", value: String(om.replied_30d) },
    {
      label: "Reply rate",
      value: om.reply_rate !== null && om.reply_rate !== undefined ? pct(om.reply_rate) : "—",
      thin: om.sent_30d < 5,
    },
    {
      label: "Avg reply · hrs",
      value: om.avg_reply_hours !== null && om.avg_reply_hours !== undefined
        ? om.avg_reply_hours.toFixed(1)
        : "—",
    },
    { label: "Booked · 12m", value: String(om.booked_12m), thin: om.win_rate_thin },
    { label: "Rejected · 12m", value: String(om.rejected_12m), thin: om.win_rate_thin },
    { label: "Revenue · booked", value: `$${Math.round(om.revenue_usd ?? 0).toLocaleString("en-US")}` },
  ]
  return (
    <Panel title="Outreach stats" description="Real numbers from sent_log + call_outcomes.">
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {tiles.map((t) => (
          <li key={t.label} className="rounded-sm border border-border bg-background p-2">
            <div className="flex items-center justify-between text-[0.65rem] uppercase tracking-wider text-muted-foreground">
              <span>{t.label}</span>
              {t.thin ? <span className="rounded-[2px] bg-muted px-1 font-semibold">thin</span> : null}
            </div>
            <div className="mt-1 font-mono text-lg font-semibold">{t.value}</div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

export function ActivityChart12m({ om }: { om: OverviewMetrics }) {
  const series = om.monthly_series
  // Chart primitive: compact, legible without a library. Booked (bottom) +
  // rejected (top) stacked bars; sent as a line overlay.
  const max = Math.max(
    1,
    ...series.map((p) => Math.max(p.booked + p.rejected, p.sent)),
  )
  const w = 24
  const gap = 4
  const h = 100
  const chartW = series.length * (w + gap)
  return (
    <Panel
      title="12-month activity"
      description="Booked + rejected per month, with sent as the overlay line."
    >
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${chartW} ${h + 20}`} className="w-full min-w-[320px]" height={h + 20}>
          {series.map((p, i) => {
            const x = i * (w + gap)
            const booked = (p.booked / max) * h
            const rejected = (p.rejected / max) * h
            const yBooked = h - booked
            const yRejected = yBooked - rejected
            return (
              <g key={p.month}>
                {p.rejected > 0 ? (
                  <rect
                    x={x}
                    y={yRejected}
                    width={w}
                    height={rejected}
                    className="fill-bad/70"
                  />
                ) : null}
                {p.booked > 0 ? (
                  <rect x={x} y={yBooked} width={w} height={booked} className="fill-good/80" />
                ) : null}
                <text
                  x={x + w / 2}
                  y={h + 12}
                  className="fill-muted-foreground text-[8px]"
                  textAnchor="middle"
                >
                  {p.month.slice(5)}
                </text>
              </g>
            )
          })}
          <polyline
            points={series
              .map((p, i) => {
                const x = i * (w + gap) + w / 2
                const y = h - (p.sent / max) * h
                return `${x},${y}`
              })
              .join(" ")}
            fill="none"
            className="stroke-chart-2"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <ul className="mt-2 flex flex-wrap gap-3 text-[0.7rem] text-muted-foreground">
        <li className="inline-flex items-center gap-1">
          <span className="size-2 rounded-[2px] bg-good/80" aria-hidden /> Booked
        </li>
        <li className="inline-flex items-center gap-1">
          <span className="size-2 rounded-[2px] bg-bad/70" aria-hidden /> Rejected
        </li>
        <li className="inline-flex items-center gap-1">
          <span className="h-0.5 w-3 bg-chart-2" aria-hidden /> Sent
        </li>
      </ul>
    </Panel>
  )
}

export function BrokerOverviewSections({
  metrics,
}: {
  metrics: OverviewMetrics | null | undefined
}) {
  if (!metrics) return null
  const hasAnyActivity =
    metrics.booked_12m + metrics.rejected_12m + metrics.sent_30d > 0 ||
    metrics.monthly_series.some((p) => p.sent + p.booked + p.rejected > 0)
  // Fully-thin + no activity → nothing meaningful to render.
  if (metrics.health_thin && !hasAnyActivity) return null
  return (
    <div className="space-y-4">
      <HealthGaugeCard om={metrics} />
      <StatTilesRow om={metrics} />
      {hasAnyActivity ? <ActivityChart12m om={metrics} /> : null}
    </div>
  )
}
