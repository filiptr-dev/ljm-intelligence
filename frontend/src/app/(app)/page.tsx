"use client"

/**
 * Overview — the Today desk.
 *
 * One aggregate read (`GET /overview/today` via the typed client) powers
 * three honest tiles, one merged "Do next" action list, and one booked-vs-
 * rejected chart. No MonitoringHero, no LiveFeed, no segments, no demo
 * `SOURCE_LIST`. Rows link to the real page that handles the action — the
 * server owns the ordering, this page never re-sorts.
 *
 * Why client-side: the API client reads a bearer token out of the live
 * SessionProvider, which only exists in the browser. A server-side fetch
 * would need a parallel cookie-forwarding path; the aggregate endpoint is
 * cheap enough that one browser fetch is the honest answer.
 */

import * as React from "react"
import Link from "next/link"
import { ArrowRight, Phone, PhoneCall, Sparkles, Truck, UserPlus, Zap } from "lucide-react"
import { toast } from "sonner"
import { PageHeader, Panel, StatTile } from "@/components/app/ui"
import { OutcomeColumns } from "@/components/charts/charts"
import { Button, buttonVariants } from "@/components/ui/button"
import { getToday, type DoNextRow, type OverviewToday } from "@/lib/api/overview"
import { cn } from "@/lib/utils"

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
  // While loading, point at sensible defaults so the chrome never blinks
  // through a broken state.
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

export default function OverviewPage() {
  const [data, setData] = React.useState<OverviewToday | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  const refresh = React.useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const d = await getToday(signal)
      setData(d)
    } catch (e) {
      if (signal?.aborted) return
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      toast.error("Couldn't load Today", { description: msg })
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    const ctrl = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh(ctrl.signal)
    return () => ctrl.abort()
  }, [refresh])

  const tiles = data?.tiles
  const doNext = data?.do_next ?? []
  const chart = data?.booked_vs_rejected

  // Shape the chart data to match OutcomeColumns' expected `Month` type.
  // Series items from the API are {month, booked, rejected} — pad the rest
  // of the Month fields with zeros so the chart type-checks (the chart only
  // reads booked/rejected, so the extra fields are inert).
  const chartData = React.useMemo(
    () =>
      (chart?.series ?? []).map((p) => ({
        month: p.month,
        booked: p.booked,
        rejected: p.rejected,
        decided: p.booked + p.rejected,
        winRate: p.booked / Math.max(1, p.booked + p.rejected),
        usdPerMile: null as number | null,
        eurPerKm: null as number | null,
      })),
    [chart?.series],
  )

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="Today"
        description="Who to call and what to chase today."
        actions={<HeaderActions data={data} />}
      />

      {error ? (
        <div className="mb-5 rounded-sm border border-destructive/50 bg-destructive/5 p-3 text-sm text-destructive">
          <div className="flex items-center justify-between gap-3">
            <span>Backend not reachable — {error}</span>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              Retry
            </Button>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <StatTile
          label="To call today"
          value={loading && !tiles ? "—" : tiles?.to_call_today.value ?? 0}
          sub={
            <Link href={tiles?.to_call_today.href ?? "/call-list"} className="hover:underline">
              Open call list <ArrowRight className="inline size-3" />
            </Link>
          }
        />
        <StatTile
          label="New leads since last crawl"
          value={loading && !tiles ? "—" : tiles?.new_leads_since_last_crawl.value ?? 0}
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
          value={loading && !tiles ? "—" : tiles?.loads_booked_90d.value ?? 0}
          sub={
            <span className="flex flex-wrap items-center gap-2">
              {tiles?.loads_booked_90d.demo ? (
                <span className="inline-flex items-center rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold tracking-wide uppercase">
                  demo
                </span>
              ) : null}
              <Link href={tiles?.loads_booked_90d.href ?? "/intelligence"} className="hover:underline">
                Open intelligence <ArrowRight className="inline size-3" />
              </Link>
            </span>
          }
        />
      </div>

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
          {loading && doNext.length === 0 ? (
            <ul className="divide-y divide-border">
              {[0, 1, 2].map((i) => (
                <li key={i} className="flex items-center gap-3 p-4">
                  <span className="size-9 shrink-0 animate-pulse rounded-sm bg-muted" aria-hidden />
                  <span className="h-4 flex-1 animate-pulse rounded-sm bg-muted" aria-hidden />
                </li>
              ))}
            </ul>
          ) : doNext.length === 0 ? (
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
                  {/* Row click target — keep this a plain Link, don't nest a
                      second <a> inside (hydration warns on nested anchors).
                      The dial button is rendered as a sibling below. */}
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
          title="Booked vs rejected · 90 days"
          description={
            chart?.demo
              ? "Not enough outcomes logged yet — chart is a placeholder."
              : "Every booked / rejected outcome, by month."
          }
          action={
            chart?.demo ? (
              <span className="inline-flex items-center rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold tracking-wide uppercase">
                demo
              </span>
            ) : null
          }
        >
          {loading && !chart ? (
            <div className="h-64 w-full animate-pulse rounded-sm bg-muted/40" aria-hidden />
          ) : chart?.demo || chartData.length === 0 ? (
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
