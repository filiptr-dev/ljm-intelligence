"use client"

import * as React from "react"
import { Building2, MessageSquareReply, Send } from "lucide-react"
import { cn } from "@/lib/utils"
import { timeAgo } from "@/lib/format"
import type { LiveFeedItem } from "@/lib/api/analysis"
import { useLiveFeed } from "./use-live-feed"

/**
 * Live feed — real backend events only.
 *
 * Pre-2026-10-06 this read `useEngine().feed`, a client-side `createRng`
 * fiction that invented `scan` / `found` / `verify` / `duplicate` /
 * `outreach` / `reply` events every 7-16s. Now it reads
 * `GET /analysis/live-feed` (rolling 12h, tenant-scoped) via
 * `useLiveFeed()`. The backend never emits `scan` / `verify` /
 * `duplicate` kinds (nothing records them), so the icon map shrinks to
 * the three real kinds — removed, not faked.
 */

type FeedKind = LiveFeedItem["kind"]

const ICON: Record<FeedKind, React.ComponentType<{ className?: string }>> = {
  found: Building2,
  outreach: Send,
  reply: MessageSquareReply,
}

function useTick(ms = 15000) {
  const [, set] = React.useState(0)
  React.useEffect(() => {
    const id = setInterval(() => set((x) => x + 1), ms)
    return () => clearInterval(id)
  }, [ms])
}

export function ScoreChip({ score }: { score: number }) {
  const tone = score >= 75 ? "bg-good text-white" : score >= 50 ? "bg-safety text-asphalt" : "bg-muted text-muted-foreground"
  return <span className={cn("num inline-flex h-5 min-w-8 items-center justify-center rounded-[3px] px-1 font-mono text-[0.7rem] font-semibold", tone)}>{score}</span>
}

export function LiveFeed({ limit = 12, className, filter }: { limit?: number; className?: string; filter?: FeedKind[] }) {
  const feed = useLiveFeed(Math.max(limit, 25))
  useTick()
  const items = (filter ? feed.filter((f) => filter.includes(f.kind)) : feed).slice(0, limit)
  return (
    <ol className={cn("divide-y divide-border", className)} aria-live="polite">
      {items.length === 0 ? (
        <li className="py-6 text-center text-sm text-muted-foreground">No activity in the last 12 hours.</li>
      ) : null}
      {items.map((e) => {
        const Icon = ICON[e.kind]
        return (
          <li key={e.id} className="flex animate-feed items-start gap-3 py-2.5">
            <span
              className={cn(
                "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-sm",
                e.kind === "found" ? "bg-safety text-asphalt" : e.kind === "reply" ? "bg-good text-white" : "bg-asphalt text-white",
              )}
            >
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className={cn("truncate text-sm", e.kind === "found" ? "font-semibold" : "")}>{e.text}</span>
              </div>
              {e.detail ? <div className="truncate text-xs text-muted-foreground">{e.detail}</div> : null}
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <span className="text-[0.7rem] text-muted-foreground">{timeAgo(e.at)}</span>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
