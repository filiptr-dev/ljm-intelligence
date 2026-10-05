"use client"

import * as React from "react"
import { Building2, Copy, MailCheck, MessageSquareReply, Radar, Send } from "lucide-react"
import { USTruck, EUTruck } from "@/components/brand/trucks"
import { Road } from "@/components/brand/marks"
import { cn } from "@/lib/utils"
import { num, timeAgo } from "@/lib/format"
import { useEngine, type FeedEvent } from "./engine"
import { useTodayCounters } from "./use-today-counters"
import { RegionTag } from "./ui"

const ICON: Record<FeedEvent["kind"], React.ComponentType<{ className?: string }>> = {
  scan: Radar, found: Building2, verify: MailCheck, duplicate: Copy, outreach: Send, reply: MessageSquareReply,
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

export function LiveFeed({ limit = 12, className, filter }: { limit?: number; className?: string; filter?: FeedEvent["kind"][] }) {
  const { feed } = useEngine()
  useTick()
  const items = (filter ? feed.filter((f) => filter.includes(f.kind)) : feed).slice(0, limit)
  return (
    <ol className={cn("divide-y divide-border", className)} aria-live="polite">
      {items.length === 0 ? (
        <li className="py-6 text-center text-sm text-muted-foreground">Connecting to sources…</li>
      ) : null}
      {items.map((e) => {
        const Icon = ICON[e.kind]
        return (
          <li key={e.id} className="flex animate-feed items-start gap-3 py-2.5">
            <span
              className={cn(
                "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-sm",
                e.kind === "found" ? "bg-safety text-asphalt" : e.kind === "reply" ? "bg-good text-white" : e.kind === "outreach" ? "bg-asphalt text-white" : "bg-muted text-muted-foreground",
              )}
            >
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className={cn("truncate text-sm", e.kind === "found" ? "font-semibold" : "")}>{e.text}</span>
                {e.score !== undefined ? <ScoreChip score={e.score} /> : null}
              </div>
              {e.detail ? <div className="truncate text-xs text-muted-foreground">{e.detail}</div> : null}
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <span className="text-[0.7rem] text-muted-foreground">{timeAgo(new Date(e.at).toISOString())}</span>
              {e.region ? <RegionTag region={e.region} /> : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export function MonitoringHero({ sources }: { sources: number }) {
  const { liveLeads } = useEngine()
  const counters = useTodayCounters()
  const us = liveLeads[0]?.region !== "EU"
  return (
    <section className="relative overflow-hidden rounded-sm bg-asphalt text-white">
      <div className="grid gap-6 p-5 md:grid-cols-[1fr_minmax(0,420px)] md:p-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-display text-sm font-semibold tracking-[0.16em] text-safety">
            <span className="size-2 animate-beacon rounded-full bg-safety" aria-hidden />
            Monitoring active
          </div>
          <h2 className="mt-2 font-display text-3xl leading-tight font-bold md:text-4xl">
            Searching {sources} sources for brokers, shippers &amp; forwarders
          </h2>
          <p className="mt-2 max-w-xl text-sm text-[#b9bcc2]">
            Every company that needs trucks is scored against your best customers the moment it is found. Brokers, direct shippers and forwarders with a high match get a personalised email automatically.
          </p>
          <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              ["Scanned today", counters.scanned],
              ["New leads", counters.found],
              ["Emails sent", counters.sent],
              ["Replies", counters.replies],
            ].map(([l, v]) => (
              <div key={l as string} className="border-l-2 border-safety pl-3">
                <dt className="text-xs text-[#8b9098]">{l}</dt>
                <dd className="num font-mono text-2xl font-semibold">{num(v as number)}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="flex flex-col justify-end">
          <div className="px-2">{us ? <USTruck spinning /> : <EUTruck spinning />}</div>
          <Road moving className="-mx-6 h-3.5 bg-[#0d0e10]" />
        </div>
      </div>
    </section>
  )
}
