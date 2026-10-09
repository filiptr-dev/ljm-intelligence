"use client"

import * as React from "react"
import dynamic from "next/dynamic"
import { ArrowDownRight, ArrowUpRight, Minus, X } from "lucide-react"
import { PageHeader, Panel, StatTile } from "@/components/app/ui"
import { Segmented } from "@/components/app/segmented"
import { LaneAiInsightsPanel, type AiState } from "@/components/app/lane-ai-insights"
import { ChartSkeleton, EmptyChart } from "@/components/charts/primitives"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  getCachedEntityInsights, getLanesHeatmap, getLanesRuns, getLanesSummary, getTopLanes, postLaneInsights,
  type HeatmapData, type LaneAiInsights, type LanesFilter, type LanesPeriod, type LanesSummary, type RunsPage, type TopLanes,
} from "@/lib/api/lanes"
import { cn } from "@/lib/utils"
import { CostBar } from "./cost-bar"
import { int, laneLabel, perMile, pct, signedPct, STATE_NAMES, usd } from "./format"
import { CostStackChart, LengthBandChart } from "./lanes-charts"
import { Statements } from "./statements"
import type { MapMode } from "@/components/charts/us-lane-map"

// deck.gl + maplibre are heavy and WebGL-only: load them on this route, in the browser, only.
const UsLaneMap = dynamic(() => import("@/components/charts/us-lane-map"), {
  ssr: false,
  loading: () => <ChartSkeleton className="h-[40rem]" />,
})

export type LanesInitial = {
  period: LanesPeriod
  filter: LanesFilter
  summary: LanesSummary
  top: TopLanes
  heat: HeatmapData
  runs: RunsPage
}

const PERIODS: { value: LanesPeriod; label: string }[] = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
]

function TrendIcon({ v }: { v: number | null | undefined }) {
  if (v == null || Math.abs(v) < 1) return <Minus className="size-3.5 text-muted-foreground" aria-label="steady" />
  return v > 0 ? <ArrowUpRight className="size-3.5 text-good" aria-label="up" /> : <ArrowDownRight className="size-3.5 text-bad" aria-label="down" />
}

function useAbortable() {
  const ref = React.useRef<AbortController | null>(null)
  return React.useCallback(() => {
    ref.current?.abort()
    ref.current = new AbortController()
    return ref.current.signal
  }, [])
}

export function LanesView({ initial }: { initial: LanesInitial }) {
  const [period, setPeriod] = React.useState<LanesPeriod>(initial.period)
  const [filter, setFilter] = React.useState<LanesFilter>(initial.filter)
  const [mode, setMode] = React.useState<MapMode>("origin")
  const [level, setLevel] = React.useState<"state" | "city">("state")
  const [summary, setSummary] = React.useState(initial.summary)
  const [top, setTop] = React.useState(initial.top)
  const [heat, setHeat] = React.useState(initial.heat)
  const [runs, setRuns] = React.useState(initial.runs)
  const [loadingMore, setLoadingMore] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  // AI result is tagged with the request it answers; a stale tag means "loading".
  const [aiResult, setAiResult] = React.useState<{ key: string; state: AiState } | null>(null)
  const [cachedInsights, setCachedInsights] = React.useState<{ key: string; map: Record<string, string[]> } | null>(null)
  const [highlightLane, setHighlightLane] = React.useState<string | null>(null)
  const first = React.useRef(true)
  const firstTop = React.useRef(true)
  const firstHeat = React.useRef(true)
  const nextSummary = useAbortable()
  const nextTop = useAbortable()
  const nextHeat = useAbortable()
  const nextAi = useAbortable()

  const filterKey = `${filter.state ?? ""}|${filter.lane ?? ""}`
  const filtered = !!(filter.state || filter.lane)

  // keep the URL shareable: ?period=&state=&lane=
  React.useEffect(() => {
    const u = new URL(window.location.href)
    u.searchParams.set("period", period)
    for (const k of ["state", "lane"] as const) {
      const v = filter[k]
      if (v) u.searchParams.set(k, v)
      else u.searchParams.delete(k)
    }
    window.history.replaceState(null, "", u)
  }, [period, filter])

  // summary + runs follow period and filter
  React.useEffect(() => {
    if (first.current) { first.current = false; return }
    const signal = nextSummary()
    setBusy(true)
    setError(null)
    Promise.all([getLanesSummary(period, filter, signal), getLanesRuns(period, filter, null, signal)])
      .then(([s, r]) => { setSummary(s); setRuns(r); setBusy(false) })
      .catch((e) => { if (!signal.aborted) { setError(String(e?.message ?? e)); setBusy(false) } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, filterKey])

  // top lanes also follow the state/city toggle
  React.useEffect(() => {
    if (firstTop.current) { firstTop.current = false; if (level === "state") return }
    const signal = nextTop()
    getTopLanes(period, level, filter, signal).then(setTop).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, filterKey, level])

  // the map stays whole (unfiltered) so you can click somewhere else; it follows the period
  React.useEffect(() => {
    if (firstHeat.current) { firstHeat.current = false; return }
    const signal = nextHeat()
    getLanesHeatmap(period, {}, signal).then(setHeat).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

  const aiKey = `${period}|${filterKey}`
  const fetchAi = React.useCallback((refresh: boolean) => {
    const signal = nextAi()
    const key = `${period}|${filterKey}`
    postLaneInsights(period, filter, refresh, signal)
      .then((data: LaneAiInsights) => setAiResult({ key, state: { phase: "done", data } }))
      .catch((e) => { if (!signal.aborted) setAiResult({ key, state: { phase: "error", message: String(e?.message ?? e) } }) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, filterKey])
  React.useEffect(() => { fetchAi(false) }, [fetchAi])
  const ai: AiState = aiResult && aiResult.key === aiKey ? aiResult.state : { phase: "loading" }
  const refreshAi = () => { setAiResult(null); fetchAi(true) }

  // Per-entity suggestions for the (unfiltered) map: this call's own when unfiltered, else the cache (no model call).
  React.useEffect(() => {
    if (!filtered) return
    const ctl = new AbortController()
    getCachedEntityInsights(period, {}, ctl.signal)
      .then((map) => setCachedInsights({ key: period, map }))
      .catch(() => {})
    return () => ctl.abort()
  }, [filtered, period])
  const mapInsights: Record<string, string[]> = !filtered
    ? (ai.phase === "done" ? ai.data.entity_insights : {})
    : cachedInsights?.key === period ? cachedInsights.map : {}

  const loadMore = async () => {
    if (!runs.next_cursor) return
    setLoadingMore(true)
    try {
      const more = await getLanesRuns(period, filter, runs.next_cursor)
      setRuns({ ...more, items: [...runs.items, ...more.items], total: more.total })
    } finally {
      setLoadingMore(false)
    }
  }

  const toggleState = (abbr: string) =>
    setFilter((f) => (f.state === abbr ? {} : { state: abbr }))
  const toggleLane = (key: string) =>
    setFilter((f) => (f.lane === key ? {} : { lane: key }))

  const k = summary.kpis
  const t = summary.trend
  const aiUnavailable = ai.phase === "error" || (ai.phase === "done" && ai.data.status === "unavailable")
  const filterLabel = filter.state ? (STATE_NAMES[filter.state] ?? filter.state) : filter.lane ? laneLabel(filter.lane) : null

  return (
    <>
      <PageHeader
        eyebrow="Analyse"
        title="Lanes history"
        description={`${int(summary.history_runs)} completed runs across ${summary.history_months} months of history — where you drive, what it costs, and what to do next.`}
        actions={<Segmented value={period} onChange={setPeriod} options={PERIODS} />}
      />

      {filterLabel ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Showing only</span>
          <button
            type="button"
            onClick={() => setFilter({})}
            className="inline-flex items-center gap-1.5 rounded-full border border-safety/40 bg-accent px-3 py-1 font-medium hover:bg-accent/70"
          >
            {filterLabel} <X className="size-3.5" aria-hidden />
            <span className="sr-only">clear filter</span>
          </button>
          <span className="text-xs text-muted-foreground">KPIs, charts, AI and the runs table follow this filter; the map stays whole.</span>
        </div>
      ) : null}
      {error ? <p role="alert" className="mb-4 rounded-sm border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad">Could not refresh: {error}</p> : null}

      <div className={cn("space-y-5 transition-opacity", busy && "opacity-60")} aria-busy={busy}>
        {k.runs === 0 ? (
          <EmptyChart title="No runs match this view" hint="Try a longer period or clear the filter above." />
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            <StatTile label="Runs" value={int(k.runs)} delta={t.runs_pct ?? undefined} deltaLabel={`${signedPct(t.runs_pct)} vs before`} sub="no earlier period to compare" />
            <StatTile label="Miles" value={int(k.miles)} sub={`${int(k.miles / Math.max(k.runs, 1))} per run`} />
            <StatTile label="Revenue" value={usd(k.revenue)} delta={t.revenue_pct ?? undefined} deltaLabel={`${signedPct(t.revenue_pct)} vs before`} sub={`${usd(k.cost_total)} cost`} />
            <StatTile label="Gross margin" value={pct(k.margin_pct, 1)} delta={t.margin_pp ?? undefined} deltaLabel={`${t.margin_pp != null && t.margin_pp > 0 ? "+" : ""}${t.margin_pp?.toFixed(1)} pts vs before`} sub={`${usd(k.margin)} after costs`} />
            <StatTile label="Revenue / mile" value={perMile(k.rate_per_mile)} delta={t.rate_pct ?? undefined} deltaLabel={`${signedPct(t.rate_pct)} vs before`} sub={`cost ${perMile(k.cost_per_mile)} / mile`} />
          </div>
        )}
        {k.runs > 0 ? <p className="-mt-2 text-xs text-muted-foreground">Arrows compare the {t.label}.</p> : null}

        <div className="grid gap-5 xl:grid-cols-3">
          <Panel className="xl:col-span-2" title="What each period cost you" description={`Fuel, driver pay, load costs and dispatch per ${period}, with revenue on top — ${summary.window_label}.`}>
            <CostStackChart buckets={summary.buckets} />
            <Statements className="mt-4" columns items={summary.statements} keys={["totals", "cost_split"]} />
          </Panel>
          <Panel title="How long your trips are" description="Runs by trip length (miles).">
            <LengthBandChart bands={summary.length_bands} />
            <Statements className="mt-4" items={summary.statements} keys={["length_band"]} />
          </Panel>
        </div>

        <Panel title="AI: what to do next" description="Named lanes and levers with numbers — computed from the aggregates only, cached for 24 hours.">
          <Statements className="mb-4" items={summary.statements} keys={["shift"]} />
          <LaneAiInsightsPanel state={ai} onRefresh={refreshAi} />
        </Panel>

        <Panel
          title="Where you drive"
          description="Hover any state, city or lane for every metric and AI advice. Click a state or lane to filter the page."
          action={
            <Segmented
              value={mode}
              onChange={setMode}
              options={[{ value: "origin", label: "Origins" }, { value: "dest", label: "Destinations" }, { value: "flows", label: "Lane flows" }]}
            />
          }
          bodyClassName="p-3"
        >
          <UsLaneMap
            data={heat}
            mode={mode}
            selectedState={filter.state}
            selectedLane={filter.lane}
            highlightLane={highlightLane}
            insights={mapInsights}
            aiUnavailable={aiUnavailable}
            onSelectState={toggleState}
            onSelectLane={toggleLane}
          />
        </Panel>

        <Panel
          title="Your top lanes"
          description={`Most-run lanes, ${top.window_label}. Click one to filter; hover to see it on the map (Lane flows).`}
          action={<Segmented value={level} onChange={setLevel} options={[{ value: "state", label: "State ↔ state" }, { value: "city", label: "City ↔ city" }]} />}
          bodyClassName="p-0"
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Lane</TableHead>
                <TableHead className="text-right">Runs</TableHead>
                <TableHead className="text-right">Miles</TableHead>
                <TableHead className="text-right">Revenue</TableHead>
                <TableHead className="text-right">$ / mile</TableHead>
                <TableHead className="text-right">Margin</TableHead>
                <TableHead className="text-right">Rate trend</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {top.lanes.map((l) => (
                <TableRow
                  key={l.key}
                  className={cn("cursor-pointer", (filter.lane === l.key) && "bg-accent")}
                  onClick={() => toggleLane(l.key)}
                  onMouseEnter={() => level === "city" && setHighlightLane(l.key)}
                  onMouseLeave={() => setHighlightLane(null)}
                >
                  <TableCell className="font-medium">{l.origin} <span className="text-muted-foreground">→</span> {l.dest}</TableCell>
                  <TableCell className="num text-right">{int(l.runs)}</TableCell>
                  <TableCell className="num text-right">{int(l.miles)}</TableCell>
                  <TableCell className="num text-right">{usd(l.revenue)}</TableCell>
                  <TableCell className="num text-right">{perMile(l.avg_rate_per_mi)}</TableCell>
                  <TableCell className="num text-right">{pct(l.margin_pct, 0)}</TableCell>
                  <TableCell className="text-right">
                    <span className="inline-flex items-center justify-end gap-1">
                      <TrendIcon v={l.trend_pct} />
                      <span className={cn("num text-xs", l.trend_pct == null ? "text-muted-foreground" : l.trend_pct >= 0 ? "text-good" : "text-bad")}>{signedPct(l.trend_pct)}</span>
                    </span>
                  </TableCell>
                </TableRow>
              ))}
              {top.lanes.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="text-sm text-muted-foreground">No lanes in this view yet.</TableCell></TableRow>
              ) : null}
            </TableBody>
          </Table>
          <Statements className="border-t border-border p-4" columns items={summary.statements} keys={["most_frequent", "most_miles"]} />
        </Panel>

        <Panel title="Every run" description={`${int(runs.total)} runs in the ${summary.window_label}, newest first.`} bodyClassName="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Pickup</TableHead>
                <TableHead>Lane</TableHead>
                <TableHead>Equipment</TableHead>
                <TableHead className="text-right">Miles</TableHead>
                <TableHead className="text-right">Revenue</TableHead>
                <TableHead className="w-44">Cost split</TableHead>
                <TableHead className="text-right">Margin</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.items.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="num whitespace-nowrap">{new Date(r.pickup_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</TableCell>
                  <TableCell className="whitespace-nowrap">{r.origin} <span className="text-muted-foreground">→</span> {r.dest}</TableCell>
                  <TableCell className="capitalize">{r.equipment ?? "—"}</TableCell>
                  <TableCell className="num text-right">{int(r.miles)}</TableCell>
                  <TableCell className="num text-right">{usd(r.revenue)}</TableCell>
                  <TableCell><CostBar compact fuel={r.cost_fuel} driver={r.cost_driver} load={r.cost_load} dispatch={r.cost_dispatch} /></TableCell>
                  <TableCell className={cn("num text-right font-medium", r.margin < 0 ? "text-bad" : "text-good")}>
                    {usd(r.margin)} <span className="text-xs font-normal text-muted-foreground">{pct(r.margin_pct, 0)}</span>
                  </TableCell>
                </TableRow>
              ))}
              {runs.items.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="text-sm text-muted-foreground">No runs match this view.</TableCell></TableRow>
              ) : null}
            </TableBody>
          </Table>
          {runs.next_cursor ? (
            <div className="flex justify-center border-t border-border p-3">
              <Button variant="outline" size="sm" disabled={loadingMore} onClick={loadMore}>
                {loadingMore ? "Loading…" : `Show more (${int(runs.total - runs.items.length)} left)`}
              </Button>
            </div>
          ) : null}
        </Panel>
      </div>
    </>
  )
}
