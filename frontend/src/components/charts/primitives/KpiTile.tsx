import * as React from "react"
import Link from "next/link"
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react"
import type { KpiBlock } from "@/lib/api/analysis"
import { cn } from "@/lib/utils"
import { Sparkline } from "./Sparkline"
import { ThinSample } from "./ThinSample"

/**
 * The one tile shape across every analytics view.
 *
 * ``KpiBlock`` carries ``value + unit + prev + delta_pct + series + thin``,
 * so a tile never has to invent its own comparison math or thin-sample
 * threshold. The server already did that work in ``kpi_service.py``.
 */
export function KpiTile({
  block,
  href,
  footer,
  upIsGood = true,
  format,
  showSparkline = true,
}: {
  block: KpiBlock
  href?: string
  footer?: React.ReactNode
  upIsGood?: boolean
  format?: (v: number) => string
  showSparkline?: boolean
}) {
  const fmt = format ?? ((v: number) => (block.unit === "%" ? `${v.toFixed(1)}%` : v.toLocaleString("en-US")))
  const direction = block.direction
  const good =
    direction === "flat" ? null : (direction === "up") === upIsGood
  const Icon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : Minus

  const prevLabel =
    block.delta_pct === null
      ? block.prev === 0
        ? "no prior data"
        : `vs ${fmt(block.prev)} prior`
      : `${block.delta_pct > 0 ? "+" : ""}${block.delta_pct.toFixed(1)}% vs ${fmt(block.prev)} prior`

  const body = (
    <div className="relative min-w-0 overflow-hidden rounded-sm border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[0.8rem] text-muted-foreground">{block.label}</div>
          <div className="mt-1.5 text-[1.9rem] leading-none font-semibold tracking-tight tabular-nums">
            {fmt(block.value)}
          </div>
          <div className="mt-1 text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
            {block.unit}
          </div>
        </div>
        {showSparkline && block.series.length > 1 ? (
          <Sparkline series={block.series} className="h-10 w-24 shrink-0" />
        ) : null}
      </div>
      <div
        className={cn(
          "mt-2 flex items-center gap-1 text-xs font-medium",
          good === null
            ? "text-muted-foreground"
            : good
              ? "text-good"
              : "text-bad",
        )}
      >
        <Icon className="size-3.5" />
        <span>{prevLabel}</span>
      </div>
      {block.thin ? <ThinSample n={countN(block)} className="mt-2" /> : null}
      {footer ? <div className="mt-2 text-xs text-muted-foreground">{footer}</div> : null}
    </div>
  )
  return href ? (
    <Link href={href} className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-chart-1">
      {body}
    </Link>
  ) : (
    body
  )
}

function countN(block: KpiBlock): number {
  if (!block.series.length) return 0
  return block.series.reduce((min, p) => Math.min(min, p.n), block.series[0].n)
}
