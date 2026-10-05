import { cn } from "@/lib/utils"

/** Loading state primitive — same height your chart renders at, so the
 * surrounding layout doesn't jump when real data arrives. */
export function ChartSkeleton({ className = "h-48" }: { className?: string }) {
  return (
    <div
      className={cn("w-full animate-pulse rounded-sm bg-muted/40", className)}
      aria-hidden
    />
  )
}
