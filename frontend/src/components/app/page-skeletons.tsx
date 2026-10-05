/**
 * Shared skeleton primitives for route-level loading.tsx files.
 *
 * Why this file exists: pages under `app/(app)/` fetch data on the server
 * with no loading.tsx of their own, so a click on a sidebar link looks
 * dead during Render's 20–50s free-tier cold start. Each navigable route
 * now has its own loading.tsx that composes these pieces to mirror the
 * real layout — no layout shift when content streams in.
 *
 * Pure markup, no "use client". Keeps the existing design tokens
 * (border-border, bg-card, Panel/PageHeader cadence) so the fallback
 * blends with the real UI.
 *
 * A group-level loading.tsx used to live at `(app)/loading.tsx` and
 * flashed on every route (commit 1c77193 removed it). The right place is
 * one level deeper — per route — which is what this file supports.
 */

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** Mirrors the <PageHeader> wrapper (eyebrow + title + description). */
export function PageHeaderSkeleton({ withArt = false, withActions = false }: { withArt?: boolean; withActions?: boolean }) {
  return (
    <div className="mb-6 flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end" aria-hidden>
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-9 w-72 max-w-full" />
        <Skeleton className="h-3 w-full max-w-xl" />
        <Skeleton className="h-3 w-2/3 max-w-md" />
      </div>
      {withArt ? <Skeleton className="hidden h-24 w-64 shrink-0 xl:block" /> : null}
      {withActions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Skeleton className="h-9 w-28" />
          <Skeleton className="h-9 w-24" />
        </div>
      ) : null}
    </div>
  )
}

/** A row of N stat tiles, same shape as <StatTile>. */
export function TilesSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-3 sm:grid-cols-2",
        count >= 4 && "lg:grid-cols-4",
        count === 3 && "lg:grid-cols-3",
        className,
      )}
      aria-hidden
    >
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-sm border border-border bg-card p-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3 h-7 w-16" />
          <Skeleton className="mt-3 h-3 w-32" />
        </div>
      ))}
    </div>
  )
}

/** Table skeleton inside a Panel-ish frame. */
export function TableSkeleton({
  rows = 10,
  cols = 5,
  withHeader = true,
}: {
  rows?: number
  cols?: number
  withHeader?: boolean
}) {
  return (
    <div className="overflow-hidden" aria-hidden>
      {withHeader ? (
        <div
          className="grid gap-3 border-b border-border bg-muted/30 px-4 py-3"
          style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: cols }).map((_, i) => (
            <Skeleton key={i} className="h-3 w-20" />
          ))}
        </div>
      ) : null}
      <div className="divide-y divide-border">
        {Array.from({ length: rows }).map((_, r) => (
          <div
            key={r}
            className="grid gap-3 px-4 py-3"
            style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
          >
            {Array.from({ length: cols }).map((_, c) => (
              <Skeleton key={c} className="h-4 w-full max-w-[12rem]" />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

/** A Panel-shaped block with an arbitrary body. */
export function PanelSkeleton({
  title = <Skeleton className="h-4 w-32" />,
  description,
  children,
  className,
  bodyClassName,
}: {
  title?: React.ReactNode
  description?: React.ReactNode
  children: React.ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <Panel
      title={title}
      description={description ?? "Loading…"}
      className={className}
      bodyClassName={bodyClassName}
    >
      {children}
    </Panel>
  )
}

/** Common "list of items inside a Panel" skeleton. */
export function ListInsidePanel({ rows = 6 }: { rows?: number }) {
  return (
    <ul className="divide-y divide-border" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i} className="flex items-center gap-3 p-4">
          <Skeleton className="size-9 shrink-0 rounded-sm" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-6 w-16 shrink-0" />
        </li>
      ))}
    </ul>
  )
}

/** ToolPlaceholder page shape: header + two side-by-side panels of bullets. */
export function ToolPlaceholderSkeleton() {
  return (
    <>
      <PageHeaderSkeleton withActions />
      <div className="grid gap-5 lg:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <PanelSkeleton key={i}>
            <ul className="space-y-3" aria-hidden>
              {Array.from({ length: 4 }).map((_, j) => (
                <li key={j} className="flex items-start gap-2">
                  <Skeleton className="mt-1 size-2 shrink-0 rounded-full" />
                  <Skeleton className="h-3 w-full max-w-sm" />
                </li>
              ))}
            </ul>
          </PanelSkeleton>
        ))}
      </div>
    </>
  )
}
