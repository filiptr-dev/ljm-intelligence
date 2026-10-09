"use client"

/**
 * Broker detail — Email analytics panel.
 *
 * Honest-by-design: every missing numeric cell renders ``—``; when the broker
 * has zero sends, the weekly chart still renders its (empty) axes beside a
 * ``— · no data yet`` label. The panel is never conditionally hidden.
 *
 * Backs commit ``13cefb9`` restoring real-data email analytics on
 * ``/brokers/[id]``. Data comes from the regenerated typed openapi-fetch
 * client via ``BrokerDetail.email_analytics``.
 */

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { cn } from "@/lib/utils"
import type { EmailAnalytics } from "@/lib/api/brokers"

const DOW_LABEL = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
const SAMPLE_FLOOR = 10

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—"
  return `${Math.round(v * 100)}%`
}

function fmtHours(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—"
  if (v < 1) return `${Math.round(v * 60)}m`
  return `${v.toFixed(1)}h`
}

function fmtHourET(h: number): string {
  const hh = h % 12 === 0 ? 12 : h % 12
  return `${hh}${h < 12 ? "am" : "pm"} ET`
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-background p-3">
      <div className="text-[0.75rem] text-muted-foreground">{label}</div>
      <div className="mt-1 text-[1.4rem] leading-none font-semibold tracking-tight">{value}</div>
      {sub ? <div className="mt-1 text-[0.7rem] text-muted-foreground">{sub}</div> : null}
    </div>
  )
}

function WeeklyChart({ series }: { series: EmailAnalytics["weekly_series_12w"] }) {
  const totalSent = series.reduce((n, p) => n + p.sent, 0)
  const maxSent = series.reduce((n, p) => Math.max(n, p.sent), 0)
  const empty = totalSent === 0
  return (
    <div>
      <div className="flex items-end gap-1 h-24">
        {series.map((p) => {
          const h = maxSent > 0 ? Math.max(2, (p.sent / maxSent) * 96) : 2
          const replyH = p.sent > 0 ? (p.replied / p.sent) * h : 0
          return (
            <div
              key={p.week_start}
              className="relative flex-1 min-w-0 rounded-t-[2px] bg-muted"
              style={{ height: `${h}px` }}
              title={`${p.week_start} · sent ${p.sent} · replied ${p.replied}`}
            >
              {replyH > 0 ? (
                <div
                  className="absolute bottom-0 left-0 right-0 rounded-t-[2px] bg-chart-1"
                  style={{ height: `${replyH}px` }}
                />
              ) : null}
            </div>
          )
        })}
      </div>
      <div className="mt-2 flex items-center justify-between text-[0.7rem] text-muted-foreground">
        <span>{series[0]?.week_start ?? "—"}</span>
        <span className={cn(empty && "italic")}>
          {empty ? "— · no data yet" : "sent · replied (12 weeks, Mon-anchored ET)"}
        </span>
        <span>{series[series.length - 1]?.week_start ?? "—"}</span>
      </div>
    </div>
  )
}

export function BrokerEmailAnalyticsPanel({ ea }: { ea: EmailAnalytics | null | undefined }) {
  if (!ea) {
    return (
      <Panel title="Email analytics" description="Sent · reply rate · timing · cadence — from real sends.">
        <p className="text-sm text-muted-foreground">— · no data yet</p>
      </Panel>
    )
  }

  // Trust the backend's floor: it returns `null` for best_day_of_week /
  // best_hour_et when the broker's all-time send count is below the sample
  // floor. Render whatever the backend sent — no second guess here.
  const dowBucket = ea.best_day_of_week
  const hourBucket = ea.best_hour_et

  return (
    <Panel
      title="Email analytics"
      description="Sent · reply rate · timing · cadence — from real sends."
    >
      <div className="space-y-4">
        {/* Volume + reply rates */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Sent 30d" value={ea.sent_30d || "—"} sub={`${ea.replied_30d || "—"} replied`} />
          <Stat label="Reply rate 30d" value={fmtPct(ea.reply_rate_30d)} />
          <Stat label="Sent 90d" value={ea.sent_90d || "—"} sub={`${ea.replied_90d || "—"} replied`} />
          <Stat label="Reply rate 90d" value={fmtPct(ea.reply_rate_90d)} />
        </div>

        {/* Reply timing */}
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Avg reply time" value={fmtHours(ea.avg_reply_hours)} />
          <Stat label="Median reply time" value={fmtHours(ea.median_reply_hours)} />
        </div>

        {/* Best day/hour (ET) */}
        <div className="grid grid-cols-2 gap-2">
          <Stat
            label="Best day"
            value={
              dowBucket && dowBucket.dow !== null && dowBucket.dow !== undefined
                ? DOW_LABEL[dowBucket.dow]
                : "—"
            }
            sub={
              dowBucket
                ? `${fmtPct(dowBucket.reply_rate)} reply · n=${dowBucket.sample}`
                : `n < ${SAMPLE_FLOOR}`
            }
          />
          <Stat
            label="Best hour"
            value={
              hourBucket && hourBucket.hour !== null && hourBucket.hour !== undefined
                ? fmtHourET(hourBucket.hour)
                : "—"
            }
            sub={
              hourBucket
                ? `${fmtPct(hourBucket.reply_rate)} reply · n=${hourBucket.sample}`
                : `n < ${SAMPLE_FLOOR}`
            }
          />
        </div>

        {/* 12-week cadence */}
        <div>
          <div className="mb-2 text-[0.8rem] text-muted-foreground">Cadence · last 12 weeks</div>
          <WeeklyChart series={ea.weekly_series_12w} />
        </div>
      </div>
    </Panel>
  )
}
