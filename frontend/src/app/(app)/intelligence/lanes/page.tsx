import { PageHeader } from "@/components/app/ui"
import { EmptyChart } from "@/components/charts/primitives"
import { LanesView } from "@/components/app/lanes/lanes-view"
import { getLanesHeatmap, getLanesRuns, getLanesSummary, getTopLanes, type LanesFilter, type LanesPeriod } from "@/lib/api/lanes"

/**
 * Lanes history — server-rendered shell. Five reads in parallel (summary, top
 * lanes, map data, runs); the AI panel loads client-side after paint so a slow
 * model never blocks the page. Filters and period live in the URL so a view is
 * shareable (`?period=year&state=TX`).
 */
export default async function LanesHistoryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const sp = await searchParams
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)
  const p = one(sp.period)
  const period: LanesPeriod = p === "week" || p === "year" ? p : "month"
  const filter: LanesFilter = {}
  const state = one(sp.state)?.toUpperCase().slice(0, 8)
  const lane = one(sp.lane)?.slice(0, 300)
  if (state) filter.state = state
  else if (lane) filter.lane = lane

  const data = await Promise.all([
    getLanesSummary(period, filter),
    getTopLanes(period, "state", filter),
    getLanesHeatmap(period),
    getLanesRuns(period, filter),
  ]).then(
    ([summary, top, heat, runs]) => ({ summary, top, heat, runs }),
    (e: unknown) => (e instanceof Error ? e : new Error("unknown error")),
  )
  if (data instanceof Error) {
    return (
      <>
        <PageHeader eyebrow="Analyse" title="Lanes history" />
        <EmptyChart
          title="Could not load lanes history"
          hint={`The API did not answer (${data.message}). Reload in a moment.`}
        />
      </>
    )
  }
  return <LanesView initial={{ period, filter, ...data }} />
}
