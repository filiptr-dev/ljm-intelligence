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
 * All read from the same ``overview_metrics`` block the list page uses
 * (one aggregate, two surfaces). Sections NEVER hide: no data renders as
 * "—" / "no data yet" so the layout matches the pre-ba15198 page 1:1.
 * The single AI panel (summary + risks + AI next step + Draft email) reads
 * GET /analysis/lead/{id} (real emails only).
 */

import * as React from "react"
import { Sparkles, TriangleAlert } from "lucide-react"
import { Panel, StatTile } from "@/components/app/ui"
import { cn } from "@/lib/utils"
import { pct, timeAgo } from "@/lib/format"
import Link from "next/link"
import type { OverviewMetrics } from "@/lib/api/brokers"
import * as analysis from "@/lib/api/analysis"

const COMPONENT_LABEL: Record<string, string> = {
  recency: "Recency",
  win_rate: "Win rate",
  tone: "Tone",
  volume: "Volume",
}

export function HealthGaugeCard({ om }: { om: OverviewMetrics | null }) {
  if (!om) {
    return (
      <Panel title="Relationship health" description="Recency + win rate + tone + volume (30/30/20/20).">
        <p className="text-sm text-muted-foreground">— · no data yet</p>
      </Panel>
    )
  }
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

/**
 * The original six tiles (pre-ba15198). Every value is real or "—":
 * there is no stored avg-booked-rate yet, so that tile stays "—".
 */
export function BrokerStatTiles({ om }: { om: OverviewMetrics | null }) {
  const decided = om ? om.booked_12m + om.rejected_12m : 0
  const winRate = om && decided > 0 && om.win_rate != null ? pct(om.win_rate) : "—"
  const lastContact = om?.last_contact_at ? timeAgo(om.last_contact_at) : "—"
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
      <StatTile
        label="Loads booked"
        value={om ? om.booked_12m : "—"}
        sub={om ? "Last 12 months" : "no data yet"}
      />
      <StatTile
        label="Win rate"
        value={winRate}
        sub={decided > 0 ? `${decided} decided${om?.win_rate_thin ? " · thin" : ""}` : "no data yet"}
      />
      <StatTile
        label="Revenue"
        value={om && om.revenue_usd > 0 ? `$${Math.round(om.revenue_usd).toLocaleString("en-US")}` : "—"}
        sub={om && om.revenue_usd > 0 ? "All booked loads" : "no data yet"}
      />
      <StatTile label="Avg booked rate" value="—" sub="no data yet" />
      <StatTile
        label="Your reply time"
        value={om?.avg_reply_hours != null ? `${om.avg_reply_hours.toFixed(1)} h` : "—"}
        sub={om?.avg_reply_hours != null ? "Average" : "no data yet"}
      />
      <StatTile
        label="Last contact"
        value={lastContact}
        sub={om?.last_contact_at ? new Date(om.last_contact_at).toLocaleDateString() : "no data yet"}
      />
    </div>
  )
}

export function ActivityChart12m({ om }: { om: OverviewMetrics | null }) {
  const series = om?.monthly_series ?? []
  const empty = !series.some((p) => p.sent + p.booked + p.rejected > 0)
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
      {empty ? <p className="mb-2 text-sm text-muted-foreground">— · no data yet</p> : null}
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

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export type NextStepFallback = { label: string; detail: string }

/**
 * The single AI panel: summary + risks + AI-written recommended next step +
 * an always-visible Draft email button. With an email on file the button
 * opens the generated draft in one click; without one an inline address
 * field replaces the (never disabled) button.
 */
export function AiSummaryCard({
  brokerId,
  email,
  fallbackStep,
  onDraft,
}: {
  brokerId: string
  email: string | null
  fallbackStep: NextStepFallback
  onDraft: (email: string) => void
}) {
  const [data, setData] = React.useState<analysis.LeadAiSummary | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [failed, setFailed] = React.useState(false)
  const [tick, setTick] = React.useState(0)
  const [addr, setAddr] = React.useState("")
  const refreshRef = React.useRef(false)

  React.useEffect(() => {
    let cancelled = false
    const refresh = refreshRef.current
    refreshRef.current = false
    analysis
      .getLeadAiSummary(brokerId, refresh)
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [brokerId, tick])

  const reload = (refresh: boolean) => {
    refreshRef.current = refresh
    setLoading(true)
    setFailed(false)
    setTick((t) => t + 1)
  }

  const ok = !loading && !failed && data?.status === "ok"
  const step = ok && data?.next_step ? data.next_step : null
  const risks = ok ? (data?.risks ?? []) : []

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Sparkles className="size-4 text-chart-2" /> AI summary
        </span>
      }
      description="Written from this broker's real emails only."
      action={
        <button
          type="button"
          onClick={() => reload(true)}
          disabled={loading}
          className="rounded-sm border border-border px-2 py-0.5 text-[0.68rem] font-medium hover:bg-muted disabled:opacity-50"
        >
          Refresh
        </button>
      }
    >
      {loading ? (
        <div className="h-16 animate-pulse rounded-sm bg-muted/40" aria-hidden />
      ) : failed || !data ? (
        <p className="text-sm text-muted-foreground">
          Couldn&apos;t load the summary.{" "}
          <button type="button" onClick={() => reload(false)} className="underline">
            Retry
          </button>
        </p>
      ) : data.status === "empty" ? (
        <p className="text-sm text-muted-foreground">No emails with this broker yet.</p>
      ) : data.status === "unavailable" ? (
        <p className="text-sm text-muted-foreground">
          {data.ai_error === "provider_null" ? (
            <>
              AI provider not configured.{" "}
              <Link href="/settings" className="underline">
                Open Settings
              </Link>
            </>
          ) : (
            <>
              Summary unavailable ({data.ai_error}).{" "}
              <button type="button" onClick={() => reload(true)} className="underline">
                Retry
              </button>
            </>
          )}
        </p>
      ) : (
        <div className="space-y-2">
          <p className="text-[0.95rem] leading-relaxed">{data.summary}</p>
          {risks.length > 0 ? (
            <ul className="mt-3 space-y-1.5">
              {risks.map((r) => (
                <li key={r} className="flex items-start gap-2 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" /> {r}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="flex items-center gap-2 text-[0.68rem] text-muted-foreground">
            <span className="rounded-[2px] bg-muted px-1 font-semibold">AI-generated</span>
            <span>{data.email_count} emails</span>
            {data.generated_at ? <span>{new Date(data.generated_at).toLocaleString()}</span> : null}
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3 rounded-sm border-l-4 border-safety bg-accent px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="eyebrow text-foreground">Recommended next step</div>
          <div className="font-semibold">{step ? step.label : fallbackStep.label}</div>
          <div className="text-sm text-muted-foreground">{step ? step.detail : fallbackStep.detail}</div>
          {step ? null : (
            <div className="mt-0.5 text-[0.68rem] text-muted-foreground">rule-based · AI step not available</div>
          )}
        </div>
        {email ? (
          <button
            type="button"
            onClick={() => onDraft(email)}
            className="inline-flex h-9 items-center rounded-sm border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
          >
            Draft email
          </button>
        ) : (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (EMAIL_RE.test(addr.trim())) onDraft(addr.trim())
            }}
          >
            <input
              type="email"
              value={addr}
              onChange={(e) => setAddr(e.target.value)}
              placeholder="No email on file — enter address"
              aria-label="Broker email address"
              className="h-9 w-56 rounded-sm border border-border bg-background px-2 text-sm"
            />
            <button
              type="submit"
              className="inline-flex h-9 items-center rounded-sm border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
            >
              Draft email
            </button>
          </form>
        )}
      </div>
    </Panel>
  )
}

/** Right-rail sections: always rendered, honest empties when there's no data. */
export function BrokerOverviewSections({ metrics }: { metrics: OverviewMetrics | null | undefined }) {
  const om = metrics ?? null
  return (
    <div className="space-y-4">
      <HealthGaugeCard om={om} />
      <ActivityChart12m om={om} />
    </div>
  )
}
