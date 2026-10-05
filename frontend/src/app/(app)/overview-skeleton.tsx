/**
 * Skeleton that mirrors the Overview layout (4-tile row + two-column panel)
 * so the Suspense fallback does not cause a layout shift when the real
 * content streams in.
 *
 * Rendered synchronously on the server — pure markup, no "use client".
 */

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { Skeleton } from "@/components/ui/skeleton"

export function OverviewSkeleton() {
  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="rounded-sm border border-border bg-card p-4"
            aria-hidden
          >
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-3 h-7 w-16" />
            <Skeleton className="mt-3 h-3 w-32" />
          </div>
        ))}
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-hidden>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-sm border border-border bg-card p-4">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="mt-3 h-6 w-12" />
          </div>
        ))}
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel title="Do next" description="Loading…" bodyClassName="p-0">
          <ul className="divide-y divide-border">
            {Array.from({ length: 5 }).map((_, i) => (
              <li key={i} className="flex items-center gap-3 p-4" aria-hidden>
                <Skeleton className="size-9 shrink-0 rounded-sm" />
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Booked vs rejected" description="Loading…">
          <Skeleton className="h-64 w-full" />
        </Panel>
      </div>
    </>
  )
}
