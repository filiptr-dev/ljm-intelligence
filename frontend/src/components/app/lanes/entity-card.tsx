import { Sparkles } from "lucide-react"
import type { CityEntity, LaneEntity } from "@/lib/api/lanes"
import { cn } from "@/lib/utils"
import { CostBar } from "./cost-bar"
import { int, perMile, pct, signedPct, STATE_NAMES, usd } from "./format"

const KIND_LABEL = { state: "State", city: "City", lane: "Lane" } as const

function Metric({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[0.68rem] text-muted-foreground">{label}</dt>
      <dd className="num truncate text-[0.95rem] leading-tight font-semibold">{value}</dd>
      {sub ? <div className="text-[0.66rem] text-muted-foreground">{sub}</div> : null}
    </div>
  )
}

function Trend({ label, value, upIsGood = true }: { label: string; value: number | null | undefined; upIsGood?: boolean }) {
  const tone = value == null ? "text-muted-foreground" : value >= 0 === upIsGood ? "text-good" : "text-bad"
  return (
    <div className="flex items-baseline justify-between gap-2 text-[0.75rem]">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("num font-semibold", tone)}>{signedPct(value)}</span>
    </div>
  )
}

/**
 * Rich hover card for one map entity (amendment A2): every metric, the cost
 * split, the trend vs the previous period, the deterministic sentence, and the
 * precomputed AI suggestions. `suggestions` comes from the cached insights
 * call — nothing here ever triggers a model request.
 */
export function EntityCard({
  entity, suggestions, aiUnavailable, className,
}: {
  entity: LaneEntity | CityEntity
  suggestions?: string[]
  aiUnavailable?: boolean
  className?: string
}) {
  const m = entity.metrics
  const title = entity.kind === "state" ? (STATE_NAMES[entity.label] ?? entity.label) : entity.label
  const noun = entity.kind === "lane" ? "lane" : entity.kind === "state" ? "state" : "city"
  return (
    <div className={cn("w-[21rem] max-w-[calc(100vw-2rem)] rounded-sm border border-border bg-popover p-3.5 text-popover-foreground shadow-lg", className)}>
      <div className="mb-2 flex items-start gap-2">
        <span className="mt-0.5 rounded-[3px] bg-asphalt px-1.5 py-0.5 font-mono text-[0.62rem] font-semibold text-white">
          {KIND_LABEL[entity.kind]}
        </span>
        <h3 className="min-w-0 text-[0.95rem] leading-snug font-semibold">{title}</h3>
      </div>
      <dl className="grid grid-cols-3 gap-x-3 gap-y-2">
        <Metric label="Runs" value={int(m.runs)} />
        <Metric label="Miles" value={int(m.miles)} />
        <Metric label="Revenue" value={usd(m.revenue)} />
        <Metric label="Rate / mile" value={perMile(m.rate_per_mile)} sub={`cost ${perMile(m.cost_per_mile)}`} />
        <Metric label="Margin" value={usd(m.margin)} sub={pct(m.margin_pct, 0)} />
        <Metric label="Typical length" value={entity.dominant_band ?? "—"} sub={entity.dominant_band ? "miles" : undefined} />
      </dl>
      {entity.runs_as_origin != null && entity.runs_as_dest != null ? (
        <p className="mt-2 text-[0.72rem] text-muted-foreground">
          {int(entity.runs_as_origin)} runs start here · {int(entity.runs_as_dest)} end here
        </p>
      ) : null}
      <CostBar className="mt-3" fuel={m.cost_fuel} driver={m.cost_driver} load={m.cost_load} dispatch={m.cost_dispatch} />
      <div className="mt-3 space-y-0.5 border-t border-border pt-2">
        <div className="eyebrow mb-1">Vs previous period</div>
        <Trend label="Rate per mile" value={entity.trend.rate_pct} />
        <Trend label="Volume (runs)" value={entity.trend.runs_pct} />
        <Trend label="Revenue" value={entity.trend.revenue_pct} />
        <p className="pt-0.5 text-[0.66rem] text-muted-foreground">
          {entity.trend.label}
          {entity.trend.low_sample ? ` (small sample: ${int(entity.trend.runs_recent)} vs ${int(entity.trend.runs_base)} runs)` : ""}
        </p>
      </div>
      <p className="mt-2 border-t border-border pt-2 text-[0.78rem] leading-relaxed">{entity.text}</p>
      <div className="mt-2 rounded-sm bg-accent px-2.5 py-2">
        <div className="eyebrow mb-1 flex items-center gap-1.5 text-foreground">
          <Sparkles className="size-3 text-safety" aria-hidden /> AI suggestion
        </div>
        {suggestions?.length ? (
          <ul className="space-y-1 text-[0.8rem] leading-snug">
            {suggestions.map((s) => <li key={s}>{s}</li>)}
          </ul>
        ) : (
          <p className="text-[0.78rem] text-muted-foreground">
            {aiUnavailable ? `AI unavailable — no suggestion for this ${noun}.` : `No AI suggestion for this ${noun}.`}
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * Hover card for a state that has no row in the data: either an operating state with no runs in
 * the selected period, or a state outside the carrier's operating area. Same frame and the same
 * zeroed headline numbers as a real state, so hovering anywhere on the map answers something.
 */
export function EmptyStateCard({ abbr, outside, className }: { abbr: string; outside: boolean; className?: string }) {
  return (
    <div className={cn("w-[21rem] max-w-[calc(100vw-2rem)] rounded-sm border border-border bg-popover p-3.5 text-popover-foreground shadow-lg", className)}>
      <div className="mb-2 flex items-start gap-2">
        <span className="mt-0.5 rounded-[3px] bg-asphalt px-1.5 py-0.5 font-mono text-[0.62rem] font-semibold text-white">State</span>
        <h3 className="min-w-0 text-[0.95rem] leading-snug font-semibold">{STATE_NAMES[abbr] ?? abbr}</h3>
      </div>
      <dl className="grid grid-cols-3 gap-x-3 gap-y-2">
        <Metric label="Runs" value={int(0)} />
        <Metric label="Miles" value={int(0)} />
        <Metric label="Revenue" value={usd(0)} />
      </dl>
      <p className="mt-3 border-t border-border pt-2 text-[0.8rem] leading-relaxed">
        {outside ? "Outside operating area" : "No runs in this period"}
      </p>
      {outside ? <p className="mt-1 text-[0.7rem] text-muted-foreground">The carrier does not run freight here.</p> : null}
    </div>
  )
}
