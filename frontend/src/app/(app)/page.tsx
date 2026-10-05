/**
 * Overview — the Today desk. SERVER COMPONENT.
 *
 * The three remote reads (`/overview/today`, `/inbox/overview-kpis`,
 * `/analysis/overview`) hit the same Render FastAPI. On a cold free dyno
 * the first one eats the 20–50s boot; the other two then arrive within
 * milliseconds. We parallelise the three awaits with `Promise.allSettled`
 * so the cold start pays once, not three times — and we wrap the
 * data-dependent body in `<Suspense>` so the page shell (header, filters)
 * streams out immediately. The companion `loading.tsx` serves the same
 * skeleton while the shell itself is still cold.
 *
 * Why server-first: the typed client reads the HttpOnly session cookie
 * via `next/headers` and attaches the bearer upstream. The browser never
 * holds a JWT; one well-shaped payload reaches the client. The only
 * `"use client"` island is the Retry button (`./overview-retry`), which
 * just calls `router.refresh()` to re-run this render.
 */

import * as React from "react"
import { Suspense } from "react"
import Link from "next/link"
import { ArrowRight, Phone, PhoneCall, Sparkles, Truck, UserPlus, Zap } from "lucide-react"
import { PageHeader, Panel, StatTile } from "@/components/app/ui"
import { FilterBar } from "@/components/analytics/FilterBar"
import { readFilters, filtersToQuery, type AnalyticsFilters } from "@/components/analytics/useFilters"
import { KpiTile } from "@/components/charts/primitives"
import { OutcomeColumns } from "@/components/charts/charts"
import { buttonVariants } from "@/components/ui/button"
import { getToday, type DoNextRow, type OverviewToday } from "@/lib/api/overview"
import * as analysis from "@/lib/api/analysis"
import * as inbox from "@/lib/api/inbox"
import { cn } from "@/lib/utils"
import { OverviewRetryButton } from "./overview-retry"
import { OverviewSkeleton } from "./overview-skeleton"

export const dynamic = "force-dynamic"

function formatSince(iso: string | null): string {
  if (!iso) return "no crawl yet — click Crawl now"
  try {
    const d = new Date(iso)
    return `Since ${d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
  } catch {
    return "Since last crawl"
  }
}

function rowIcon(kind: DoNextRow["kind"]) {
  if (kind === "call") return <Phone className="size-4" />
  if (kind === "capacity_match") return <Truck className="size-4" />
  return <UserPlus className="size-4" />
}

function rowTitle(row: DoNextRow): React.ReactNode {
  if (row.kind === "call") {
    return (
      <>
        Call <span className="font-semibold">{row.name}</span>
        <span className="text-muted-foreground"> · {row.state}</span>
      </>
    )
  }
  if (row.kind === "capacity_match") {
    return (
      <>
        Reach out to <span className="font-semibold">{row.lead_name}</span>
        <span className="text-muted-foreground"> · {row.equipment} in {row.origin_state}</span>
      </>
    )
  }
  return (
    <>
      New lead <span className="font-semibold">{row.name}</span>
      <span className="text-muted-foreground"> · {row.state} · {row.kind_label}</span>
    </>
  )
}

function rowReason(row: DoNextRow): string {
  if (row.kind === "call") return row.reason
  if (row.kind === "capacity_match") return row.reason
  return "First seen in the latest crawl"
}

function rowKey(row: DoNextRow, i: number): string {
  if (row.kind === "capacity_match") return `cap-${row.post_id}-${i}`
  return `${row.kind}-${row.lead_id}-${i}`
}

function HeaderActions({ data }: { data: OverviewToday | null }) {
  const crawl = data?.header.crawl_now_href ?? "/leads"
  const post = data?.header.new_post_href ?? "/capacity?new=1"
  return (
    <>
      <Link href={crawl} className={cn(buttonVariants({ variant: "outline" }))}>
        <Zap className="size-4" /> Crawl now
      </Link>
      <Link href={post} className={cn(buttonVariants(), "font-semibold")}>
        <ArrowRight className="size-4" /> New post
      </Link>
    </>
  )
}

/**
 * The data-heavy portion of the Overview page. Lives behind `<Suspense>`
 * so the shell flushes without waiting for the Render dyno to wake up.
 *
 * All three remote reads fire in parallel via `Promise.allSettled`: one
 * cold-start warms the dyno for all three, and a failure in any single
 * call does not swallow the others. `getToday()` already throws on
 * failure (used for the top-of-page error banner); the other two already
 * `.catch(() => null)` for graceful degradation — settling preserves
 * both shapes without extra try/catch noise.
 */
async function OverviewContent({ filters }: { filters: AnalyticsFilters }) {
  const [todayRes, inboxRes, kpiRes] = await Promise.allSettled([
    getToday(),
    inbox.overviewKpis(),
    analysis.getOverviewKpi(filtersToQuery(filters)),
  ])

  const data: OverviewToday | null =
    todayRes.status === "fulfilled" ? todayRes.value : null
  const error: string | null =
    todayRes.status === "rejected"
      ? todayRes.reason instanceof Error
        ? todayRes.reason.message
        : String(todayRes.reason)
      : null
  const inboxKpis = inboxRes.status === "fulfilled" ? inboxRes.value : null
  const kpi = kpiRes.status === "fulfilled" ? kpiRes.value : null

  const tiles = data?.tiles
  const doNext = data?.do_next ?? []

  // Prefer the KPI service's monthly series when it has data (period-aware);
  // fall back to the legacy 90d booked_vs_rejected block so the chart still
  // renders when a new tenant has nothing in the current window.
  const legacy = data?.booked_vs_rejected
  const kpiSeries = kpi?.booked_vs_rejected?.series ?? []
  const useKpi = kpiSeries.length > 0
  const chartData = useKpi
    ? kpiSeries.map((p: analysis.MetricPoint, i: number) => {
        const booked = p.value
        const n = p.n
        const rejected = Math.max(0, n - booked)
        const wrPoint = kpi?.booked_vs_rejected?.win_rate?.[i]
        return {
          month: p.key,
          booked,
          rejected,
          decided: n,
          winRate: wrPoint?.value ?? (n ? booked / n : 0),
          usdPerMile: null as number | null,
          eurPerKm: null as number | null,
        }
      })
    : (legacy?.series ?? []).map((p) => ({
        month: p.month,
        booked: p.booked,
        rejected: p.rejected,
        decided: p.booked + p.rejected,
        winRate: p.booked / Math.max(1, p.booked + p.rejected),
        usdPerMile: null as number | null,
        eurPerKm: null as number | null,
      }))

  const chartIsDemo = !useKpi && legacy?.demo === true

  return (
    <>
      {error ? (
        <div className="mb-5 rounded-sm border border-destructive/50 bg-destructive/5 p-3 text-sm text-destructive">
          <div className="flex items-center justify-between gap-3">
            <span>Backend not reachable — {error}</span>
            <OverviewRetryButton />
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="To call today"
          value={tiles?.to_call_today.value ?? 0}
          sub={
            <Link href={tiles?.to_call_today.href ?? "/call-list"} className="hover:underline">
              Open call list <ArrowRight className="inline size-3" />
            </Link>
          }
        />
        {kpi ? (
          <>
            <KpiTile block={kpi.tiles[0]} href="/leads" footer={<span>{formatSince(tiles?.new_leads_since_last_crawl.since ?? null)}</span>} />
            <KpiTile block={kpi.tiles[1]} href="/call-list" />
            <KpiTile block={kpi.tiles[2]} upIsGood={false} />
          </>
        ) : (
          <>
            <StatTile
              label="New leads since last crawl"
              value={tiles?.new_leads_since_last_crawl.value ?? 0}
              sub={
                <span className="block">
                  <span className="block text-muted-foreground">
                    {formatSince(tiles?.new_leads_since_last_crawl.since ?? null)}
                  </span>
                  <Link href={tiles?.new_leads_since_last_crawl.href ?? "/leads"} className="hover:underline">
                    View leads <ArrowRight className="inline size-3" />
                  </Link>
                </span>
              }
            />
            <StatTile
              label="Loads booked · 90 days"
              value={tiles?.loads_booked_90d.value ?? 0}
              sub={
                <Link href={tiles?.loads_booked_90d.href ?? "/intelligence"} className="hover:underline">
                  Open intelligence <ArrowRight className="inline size-3" />
                </Link>
              }
            />
            <div />
          </>
        )}
      </div>

      {inboxKpis ? (
        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Volume 7d" value={inboxKpis.volume_7d} sub={<Link href="/emails" className="hover:underline">Open inbox</Link>} />
          <StatTile label="Open threads" value={inboxKpis.open_threads} sub={<Link href="/messages" className="hover:underline">Status board</Link>} />
          <StatTile label="Urgent" value={inboxKpis.urgent} sub={<Link href="/emails?intent=urgent_truck" className="hover:underline">Triage</Link>} />
          <StatTile label="Negative tone" value={inboxKpis.negative} sub={<Link href="/intelligence" className="hover:underline">Analyze</Link>} />
        </div>
      ) : null}

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel
          title={
            <span className="flex items-center gap-2">
              <Sparkles className="size-4 text-chart-1" /> Do next
            </span>
          }
          description="Calls, truck matches and new leads, best first."
          bodyClassName="p-0"
        >
          {doNext.length === 0 ? (
            <div className="p-6 text-center">
              <p className="text-sm text-muted-foreground">
                Nothing due. Add a capacity post or run the crawler to seed activity.
              </p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                <HeaderActions data={data} />
              </div>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {doNext.map((row, i) => (
                <li
                  key={rowKey(row, i)}
                  className="flex items-center gap-3 p-4 transition-colors hover:bg-muted/40"
                >
                  <Link
                    href={row.href}
                    className="flex min-w-0 flex-1 items-center gap-3 focus-visible:outline-none"
                  >
                    <span
                      className={cn(
                        "flex size-9 shrink-0 items-center justify-center rounded-sm",
                        row.kind === "call"
                          ? "bg-safety text-asphalt"
                          : row.kind === "capacity_match"
                            ? "bg-chart-1 text-white"
                            : "bg-chart-2 text-white",
                      )}
                      aria-hidden
                    >
                      {rowIcon(row.kind)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm">{rowTitle(row)}</div>
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{rowReason(row)}</p>
                    </div>
                  </Link>
                  {row.kind === "call" ? (
                    <a
                      href={`tel:${row.phone}`}
                      className={cn(
                        buttonVariants({ variant: "outline", size: "sm" }),
                        "hidden shrink-0 self-center sm:inline-flex",
                      )}
                      aria-label={`Dial ${row.name}`}
                    >
                      <PhoneCall className="size-3.5" /> Dial
                    </a>
                  ) : null}
                </li>
              ))}
              <li className="flex items-center gap-3 bg-muted/20 p-4 text-sm text-muted-foreground">
                <span className="size-9 shrink-0 rounded-sm border border-dashed border-border" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">Replies waiting</div>
                  <p className="mt-0.5 text-xs">Coming with inbox analysis.</p>
                </div>
                <span className="shrink-0 rounded-sm border border-dashed border-border px-2 py-0.5 font-mono text-[0.65rem] tracking-wide uppercase">
                  soon
                </span>
              </li>
            </ul>
          )}
        </Panel>

        <Panel
          title="Booked vs rejected"
          description={
            chartIsDemo
              ? "Not enough outcomes logged yet — chart is a placeholder."
              : "Monthly bars with the win-rate overlay (right axis)."
          }
          action={
            chartIsDemo ? (
              <span className="inline-flex items-center rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold tracking-wide uppercase">
                demo
              </span>
            ) : null
          }
        >
          {chartIsDemo || chartData.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm text-muted-foreground">
                Log a few call outcomes and the real trend lands here.
              </p>
              <Link href="/call-list" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
                Open call list
              </Link>
            </div>
          ) : (
            <OutcomeColumns data={chartData} />
          )}
        </Panel>
      </div>
    </>
  )
}

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const sp = await searchParams
  const filters = readFilters(sp)
  const kpiPeriodLabel = filters.period === "today" ? "today" : filters.period

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="Today"
        description="Who to call and what to chase today."
        actions={<HeaderActions data={null} />}
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <FilterBar />
        <span className="text-xs text-muted-foreground">
          Comparing {kpiPeriodLabel} vs prior period
        </span>
      </div>

      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewContent filters={filters} />
      </Suspense>
    </>
  )
}
