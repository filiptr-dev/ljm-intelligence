import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * EmptyChart — the one way we say "no data in this window".
 *
 * Honest-by-design: never render a chart with zero rows that silently
 * computes 0/0 = 100%. Pages use this instead.
 */
export function EmptyChart({
  title,
  hint,
  action,
  className,
}: {
  title: string
  hint?: React.ReactNode
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex min-h-32 flex-col items-center justify-center gap-2 rounded-sm border border-dashed border-border bg-muted/20 p-4 text-center",
        className,
      )}
    >
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="max-w-sm text-xs text-muted-foreground">{hint}</p> : null}
      {action}
    </div>
  )
}
