import { cn } from "@/lib/utils"
import { int, usd } from "./format"

export const COST_KEYS = [
  { key: "fuel", label: "Fuel", color: "var(--chart-1)" },
  { key: "driver", label: "Driver", color: "var(--chart-2)" },
  { key: "load", label: "Load costs", color: "var(--chart-3)" },
  { key: "dispatch", label: "Dispatch", color: "var(--chart-4)" },
] as const

/** One stacked bar + legend for fuel / driver / load / dispatch (amounts and %). */
export function CostBar({
  fuel, driver, load, dispatch, className, compact = false,
}: { fuel: number; driver: number; load: number; dispatch: number; className?: string; compact?: boolean }) {
  const vals = { fuel, driver, load, dispatch }
  const total = fuel + driver + load + dispatch
  if (!total) return null
  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex h-2.5 w-full overflow-hidden rounded-[2px] bg-muted" role="img"
        aria-label={`Cost split: ${COST_KEYS.map((c) => `${c.label} ${Math.round((vals[c.key] / total) * 100)}%`).join(", ")}`}>
        {COST_KEYS.map((c) => (
          <span key={c.key} style={{ width: `${(vals[c.key] / total) * 100}%`, background: c.color }} />
        ))}
      </div>
      {compact ? null : (
        <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[0.72rem]">
          {COST_KEYS.map((c) => (
            <div key={c.key} className="flex items-center justify-between gap-2">
              <dt className="flex items-center gap-1.5 text-muted-foreground">
                <span className="size-2 rounded-[2px]" style={{ background: c.color }} aria-hidden />
                {c.label}
              </dt>
              <dd className="num font-medium">
                {usd(vals[c.key])} <span className="text-muted-foreground">· {int((vals[c.key] / total) * 100)}%</span>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
