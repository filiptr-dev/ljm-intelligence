"use client"

import { RefreshCw, Sparkles, TrendingDown, TrendingUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { LaneAiInsights } from "@/lib/api/lanes"
import { cn } from "@/lib/utils"
import { signedPct } from "./lanes/format"

export type AiState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "done"; data: LaneAiInsights }

function Block({ title, tone, children, empty }: { title: string; tone?: "good" | "bad"; children: React.ReactNode; empty: boolean }) {
  return (
    <section className="min-w-0 rounded-sm border border-border bg-background p-3">
      <h3 className="eyebrow mb-2 flex items-center gap-1.5">
        {tone === "good" ? <TrendingUp className="size-3.5 text-good" aria-hidden /> : null}
        {tone === "bad" ? <TrendingDown className="size-3.5 text-bad" aria-hidden /> : null}
        {title}
      </h3>
      {empty ? <p className="text-[0.8rem] text-muted-foreground">Nothing worth flagging here.</p> : <ul className="space-y-2.5">{children}</ul>}
    </section>
  )
}

function Delta({ v }: { v: number | null | undefined }) {
  if (v == null) return null
  return <span className={cn("num ml-1.5 rounded-[3px] px-1 text-[0.7rem] font-semibold", v >= 0 ? "bg-good/10 text-good" : "bg-bad/10 text-bad")}>{signedPct(v)}</span>
}

/**
 * Four short actionable blocks, never a prose summary. States: loading, ok,
 * empty, unavailable (no key / timeout / unparseable) — the deterministic
 * statements elsewhere on the page keep working in every state.
 */
export function LaneAiInsightsPanel({ state, onRefresh }: { state: AiState; onRefresh: () => void }) {
  if (state.phase === "loading") {
    return (
      <div className="grid gap-3 md:grid-cols-2" aria-busy>
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-28 animate-pulse rounded-sm bg-muted/50" />)}
      </div>
    )
  }
  if (state.phase === "error" || state.data.status === "unavailable") {
    const detail = state.phase === "error" ? state.message : state.data.ai_error
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-sm border border-dashed border-border bg-muted/30 px-4 py-3">
        <Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1 text-sm">
          <p className="font-medium">AI unavailable — check your provider in Settings.</p>
          <p className="text-xs text-muted-foreground">
            The numbers and plain-language summaries on this page do not need AI.{detail ? ` (${detail})` : ""}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={onRefresh}><RefreshCw className="size-3.5" /> Try again</Button>
      </div>
    )
  }
  const d = state.data
  if (d.status === "empty") {
    return <p className="rounded-sm border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">No runs in this window yet — nothing for the AI to analyse.</p>
  }
  return (
    <div>
      <div className="grid gap-3 md:grid-cols-2">
        <Block title="Focus lanes" tone="good" empty={!d.focus_lanes.length}>
          {d.focus_lanes.map((l) => (
            <li key={l.lane} className="text-[0.85rem] leading-snug">
              <span className="font-semibold">{l.lane}</span><Delta v={l.delta_pct} />
              <div className="text-muted-foreground">{l.why}</div>
            </li>
          ))}
        </Block>
        <Block title="Declining lanes" tone="bad" empty={!d.declining_lanes.length}>
          {d.declining_lanes.map((l) => (
            <li key={l.lane} className="text-[0.85rem] leading-snug">
              <span className="font-semibold">{l.lane}</span><Delta v={l.delta_pct} />
              <div className="text-muted-foreground">{l.why}</div>
            </li>
          ))}
        </Block>
        <Block title="Market shifts" empty={!d.market_shifts.length}>
          {d.market_shifts.map((s) => (
            <li key={s.headline} className="text-[0.85rem] leading-snug">
              <span className="font-semibold">{s.headline}</span>
              {s.evidence ? <div className="text-muted-foreground">{s.evidence}</div> : null}
            </li>
          ))}
        </Block>
        <Block title="Cost levers" empty={!d.cost_levers.length}>
          {d.cost_levers.map((c) => (
            <li key={c.lever} className="text-[0.85rem] leading-snug">
              <span className="font-semibold">{c.lever}</span>
              {c.impact_hint ? <div className="text-muted-foreground">{c.impact_hint}</div> : null}
            </li>
          ))}
        </Block>
      </div>
      <div className="mt-2.5 flex items-center justify-between gap-3 text-[0.7rem] text-muted-foreground">
        <span>
          {d.cached ? "Cached" : "Fresh"}
          {d.generated_at ? ` · ${new Date(d.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}` : ""}
          {" · hover the map for per-state and per-lane advice"}
        </span>
        <Button size="sm" variant="ghost" onClick={onRefresh}><RefreshCw className="size-3" /> Refresh</Button>
      </div>
    </div>
  )
}
