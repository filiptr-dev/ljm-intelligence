import type { PeriodLabel } from "@/lib/api/analysis"

export type AnalyticsFilters = {
  period: PeriodLabel
  from?: string
  to?: string
  region?: string
  broker?: string
  staff?: string
}

const PERIOD_SET: ReadonlySet<PeriodLabel> = new Set(["today", "7d", "30d", "90d", "custom"])

/**
 * Server-side parser — the server component reads ``searchParams`` and gives
 * the result to the analytics API client **and** to the client FilterBar
 * (which re-reads it from the URL on mount). One shape across every view.
 */
export function readFilters(sp: Record<string, string | string[] | undefined>): AnalyticsFilters {
  const raw = typeof sp.period === "string" ? sp.period : undefined
  const period: PeriodLabel = raw && PERIOD_SET.has(raw as PeriodLabel) ? (raw as PeriodLabel) : "7d"
  return {
    period,
    from: typeof sp.from === "string" ? sp.from : undefined,
    to: typeof sp.to === "string" ? sp.to : undefined,
    region: typeof sp.region === "string" ? sp.region : undefined,
    broker: typeof sp.broker === "string" ? sp.broker : undefined,
    staff: typeof sp.staff === "string" ? sp.staff : undefined,
  }
}

export function filtersToQuery(f: AnalyticsFilters): { period?: PeriodLabel; from?: string; to?: string; region?: string } {
  const q: { period?: PeriodLabel; from?: string; to?: string; region?: string } = { period: f.period }
  if (f.from) q.from = f.from
  if (f.to) q.to = f.to
  if (f.region) q.region = f.region
  return q
}

export function periodLabel(p: PeriodLabel): string {
  return (
    {
      today: "Today",
      "7d": "Last 7 days",
      "30d": "Last 30 days",
      "90d": "Last 90 days",
      custom: "Custom",
    } as const
  )[p]
}
