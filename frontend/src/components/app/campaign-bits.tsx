import { cn } from "@/lib/utils"
import type { CampaignStatus, CampaignSummary } from "@/lib/campaigns/metrics"
import { GOAL_LABEL, REPLY_COLOR, REPLY_LABEL, type ReplyCategory } from "@/lib/campaigns/types"

export function StatusPill({ status }: { status: CampaignStatus }) {
  const cls = {
    Scheduled: "border-chart-1/40 bg-chart-1/10 text-chart-1",
    Sending: "border-asphalt bg-asphalt text-white",
    Active: "border-good/40 bg-good/10 text-good",
    Completed: "border-border bg-muted text-muted-foreground",
  }[status]
  return (
    <span className={cn("inline-flex h-5 items-center gap-1 rounded-[3px] border px-1.5 text-[0.68rem] font-semibold whitespace-nowrap", cls)}>
      {status === "Sending" || status === "Active" ? <span className="size-1.5 animate-beacon rounded-full bg-current" aria-hidden /> : null}
      {status}
    </span>
  )
}

export function GoalBar({ goal, compact = false }: { goal: CampaignSummary["goal"]; compact?: boolean }) {
  if (!goal) return <span className="text-xs text-muted-foreground">No goal</span>
  const done = goal.pct >= 1
  return (
    <div className={cn("min-w-0", compact ? "w-40" : "w-full")}>
      <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
        <span className="truncate text-muted-foreground">{GOAL_LABEL[goal.type]}</span>
        <span className="num shrink-0 font-mono font-semibold">
          {goal.current}/{goal.target}
        </span>
      </div>
      <div className="h-2 bg-muted">
        <div className={cn("h-full rounded-r-[2px]", done ? "bg-good" : "bg-safety")} style={{ width: `${Math.max(2, goal.pct * 100)}%` }} />
      </div>
    </div>
  )
}

export function ReplyChip({ category }: { category: ReplyCategory }) {
  return (
    <span className="inline-flex h-5 items-center gap-1.5 rounded-[3px] border border-border bg-background px-1.5 text-[0.7rem] font-semibold whitespace-nowrap">
      <span className="size-2 rounded-[2px]" style={{ background: REPLY_COLOR[category] }} aria-hidden />
      {REPLY_LABEL[category]}
    </span>
  )
}

export const pctText = (v: number) => `${Math.round(v * 100)}%`
