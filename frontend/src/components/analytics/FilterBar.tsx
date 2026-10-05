"use client"

import * as React from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import type { PeriodLabel } from "@/lib/api/analysis"
import { periodLabel } from "./useFilters"

const PERIODS: PeriodLabel[] = ["today", "7d", "30d", "90d"]

/**
 * Shared analytics filter bar. ``"use client"`` island — it reads the URL
 * query, lets the user pick a period / region, and pushes the choice back
 * into the URL. Server components re-render against the new ``searchParams``
 * on navigation.
 *
 * Why URL-first: a shared link reproduces the exact view. No zustand, no
 * local storage; one source of truth.
 */
export function FilterBar({
  showRegion = false,
  className,
}: {
  showRegion?: boolean
  className?: string
}) {
  const router = useRouter()
  const pathname = usePathname()
  const sp = useSearchParams()
  const current = (sp.get("period") as PeriodLabel | null) ?? "7d"
  const region = sp.get("region") ?? ""

  const setPeriod = (p: PeriodLabel) => {
    const next = new URLSearchParams(sp.toString())
    next.set("period", p)
    if (p !== "custom") {
      next.delete("from")
      next.delete("to")
    }
    router.push(`${pathname}?${next.toString()}`)
  }

  const setRegion = (r: string) => {
    const next = new URLSearchParams(sp.toString())
    if (r) next.set("region", r)
    else next.delete("region")
    router.push(`${pathname}?${next.toString()}`)
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <div
        className="inline-flex overflow-hidden rounded-sm border border-border bg-background"
        role="radiogroup"
        aria-label="Date range"
      >
        {PERIODS.map((p) => (
          <button
            key={p}
            type="button"
            role="radio"
            aria-checked={current === p}
            onClick={() => setPeriod(p)}
            className={cn(
              "px-2.5 py-1 text-xs font-medium transition-colors",
              current === p ? "bg-asphalt text-white" : "hover:bg-muted",
            )}
          >
            {periodLabel(p)}
          </button>
        ))}
      </div>
      {showRegion ? (
        <div className="inline-flex overflow-hidden rounded-sm border border-border bg-background" role="radiogroup" aria-label="Region">
          {[
            { k: "", label: "All regions" },
            { k: "US", label: "US (USD)" },
            { k: "EU", label: "EU (EUR)" },
          ].map((o) => (
            <button
              key={o.k || "all"}
              type="button"
              role="radio"
              aria-checked={region === o.k}
              onClick={() => setRegion(o.k)}
              className={cn(
                "px-2.5 py-1 text-xs font-medium",
                region === o.k ? "bg-asphalt text-white" : "hover:bg-muted",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
