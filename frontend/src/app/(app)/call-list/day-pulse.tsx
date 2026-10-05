import type { DayPulse as DayPulseT } from "@/lib/api/analysis"
import { cn } from "@/lib/utils"

/**
 * Today's call-outcome pulse — the strip above the ranked queue.
 *
 * Numbers come from ``/analysis/day-pulse`` (today in ET vs yesterday in ET).
 * No hardcoded demo numbers. When yesterday has zero logs we say so instead
 * of printing a bogus "+100%" delta.
 */
export function DayPulse({ pulse }: { pulse: DayPulseT }) {
  const items: { key: string; label: string; good: boolean }[] = [
    { key: "booked", label: "Booked", good: true },
    { key: "callback", label: "Callbacks", good: true },
    { key: "not_interested", label: "Not interested", good: false },
    { key: "no_answer", label: "No answer", good: false },
  ]
  const today = pulse.today as Record<string, number>
  const yesterday = pulse.yesterday as Record<string, number>
  const conv = pulse.conversion_today
  const prevConv = pulse.conversion_yesterday
  const convDelta = conv - prevConv
  return (
    <section className="mb-4 rounded-sm border border-border bg-card p-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Today ({pulse.today_et})</h2>
        <span className="text-xs text-muted-foreground">vs yesterday</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {items.map((it) => {
          const t = today[it.key] ?? 0
          const y = yesterday[it.key] ?? 0
          const d = t - y
          const sign = d > 0 ? "+" : ""
          return (
            <div key={it.key} className="rounded-sm border border-border/60 bg-background p-2">
              <div className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">{it.label}</div>
              <div className="mt-0.5 text-xl font-semibold tabular-nums">{t}</div>
              <div
                className={cn(
                  "text-[0.68rem]",
                  d === 0
                    ? "text-muted-foreground"
                    : d > 0 === it.good
                      ? "text-good"
                      : "text-bad",
                )}
              >
                {sign}
                {d} vs {y}
              </div>
            </div>
          )
        })}
        <div className="rounded-sm border border-border/60 bg-background p-2">
          <div className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">Conversion</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums">{(conv * 100).toFixed(0)}%</div>
          <div className={cn("text-[0.68rem]", convDelta === 0 ? "text-muted-foreground" : convDelta > 0 ? "text-good" : "text-bad")}>
            {convDelta >= 0 ? "+" : ""}
            {(convDelta * 100).toFixed(0)}pp vs {(prevConv * 100).toFixed(0)}%
          </div>
        </div>
      </div>
    </section>
  )
}
