import { cn } from "@/lib/utils"
import type { LaneStatement } from "@/lib/api/lanes"

/**
 * Plain-language statements next to a chart (amendment A1). Written on the
 * server from the same aggregates as the chart, so they never disagree with
 * it and they read fine with no AI key.
 */
export function Statements({
  items, keys, className, columns = false,
}: { items: LaneStatement[]; keys: string[]; className?: string; columns?: boolean }) {
  const rows = keys.map((k) => items.find((s) => s.key === k)).filter((s): s is LaneStatement => !!s)
  if (!rows.length) return null
  return (
    <div className={cn(columns ? "grid gap-3 md:grid-cols-2" : "space-y-3", className)}>
      {rows.map((s) => (
        <div key={s.key} className="min-w-0 rounded-sm border-l-2 border-safety bg-muted/40 px-3 py-2">
          <div className="eyebrow mb-0.5">{s.title}</div>
          <p className="text-[0.88rem] leading-relaxed">{s.text}</p>
        </div>
      ))}
    </div>
  )
}
