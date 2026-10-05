import * as React from "react"
import { AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"

/**
 * ThinSample — one place to say "the data behind this number is thin".
 *
 * Why: before this primitive, a 1/1 win-rate ("100%") looked identical to a
 * 180/200 win-rate. ``n`` is structural on every ``MetricPoint`` now; this
 * strip surfaces it visibly whenever a tile or chart is below the threshold.
 */
export function ThinSample({ n, min, className }: { n: number; min?: number; className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-sm border border-dashed border-warn bg-warn/10 px-2 py-1 text-[0.68rem] text-asphalt dark:text-foreground",
        className,
      )}
      role="note"
    >
      <AlertTriangle className="size-3 shrink-0" />
      <span>
        Thin sample: n={n}
        {min ? ` (min ${min})` : ""} — treat as directional.
      </span>
    </div>
  )
}
