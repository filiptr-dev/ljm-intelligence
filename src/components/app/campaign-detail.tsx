"use client"

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"
import { ArrowLeft, CalendarClock, Copy, Forward, Sparkles } from "lucide-react"
import { Gauge } from "@/components/brand/marks"
import { Button, buttonVariants } from "@/components/ui/button"
import { summarize } from "@/lib/campaigns/metrics"
import { CAMPAIGN_TYPE_LABEL, GOAL_LABEL, POSITIVE, REPLY_COLOR, REPLY_LABEL, TONE_LABEL, type Campaign, type ReplyCategory } from "@/lib/campaigns/types"
import { LEAD_KIND_LABEL } from "@/lib/data/types"
import { num, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import { pctText, ReplyChip, StatusPill } from "./campaign-bits"
import { EmailPreview, readableTags } from "./email-preview"
import { useEngine } from "./engine"
import { SentimentDot } from "./intent"
import { BarList, Panel, RegionTag, StackBar } from "./ui"

const DAY = 86_400_000
const when = (ms: number) =>
  new Date(ms).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

export function CampaignDetail({ id, history }: { id: string; history: Campaign | null }) {
  const { campaigns, sendFollowUp } = useEngine()
  const c = history ?? campaigns.find((x) => x.id === id)
  const [filter, setFilter] = React.useState<ReplyCategory | "all">("all")

  if (!c) {
    return (
      <div className="py-20 text-center">
        <p className="text-muted-foreground">Loading campaign…</p>
        <Link href="/campaigns" className="mt-3 inline-block text-sm text-chart-1 hover:underline">Back to campaigns</Link>
      </div>
    )
  }

  const s = summarize(c)
  const replies = c.recipients.filter((r) => r.reply).sort((a, b) => b.reply!.at - a.reply!.at)
  const shown = filter === "all" ? replies : replies.filter((r) => r.reply!.category === filter)
  const startAt = c.schedule?.at ?? c.createdAt
  const pendingFollowUp = c.followUps?.[c.followUpsSent ?? 0]
  const followUpAt = pendingFollowUp ? startAt + (c.followUps ?? []).slice(0, (c.followUpsSent ?? 0) + 1).reduce((a, f) => a + f.afterDays, 0) * DAY : undefined
  const canFollowUp = !c.historical && s.queued === 0 && s.nonResponders > 0 && !!pendingFollowUp

  const positiveLanes = new Map<string, number>()
  replies.filter((r) => POSITIVE.includes(r.reply!.category) && r.lane).forEach((r) => positiveLanes.set(r.lane!, (positiveLanes.get(r.lane!) ?? 0) + 1))
  const [topLane, topLaneCount] = [...positiveLanes.entries()].sort((a, b) => b[1] - a[1])[0] ?? []

  const breakdown = (key: (r: Campaign["recipients"][number]) => string | undefined) => {
    const m = new Map<string, { n: number; rep: number }>()
    c.recipients.filter((r) => r.status !== "queued" && r.status !== "bounced").forEach((r) => {
      const k = key(r)
      if (!k) return
      const g = m.get(k) ?? { n: 0, rep: 0 }
      g.n++
      if (r.reply) g.rep++
      m.set(k, g)
    })
    return [...m.entries()].map(([label, g]) => ({ label, value: Math.round((g.rep / g.n) * 100), sub: `${g.rep} of ${g.n} replied` }))
  }

  const doFollowUp = () => {
    const n = sendFollowUp(c.id)
    toast.success(`Follow-up sent to ${n} people who haven't replied`, { description: "People who already replied are skipped automatically." })
  }

  return (
    <>
      <Link href="/campaigns" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All campaigns
      </Link>
      <div className="mb-5 flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <StatusPill status={s.status} />
            <span className="text-sm text-muted-foreground" suppressHydrationWarning>
              {CAMPAIGN_TYPE_LABEL[c.type] ?? c.type}
              {c.tone ? ` · ${TONE_LABEL[c.tone]} tone` : ""} · {s.status === "Scheduled" ? "starts" : "started"} {when(startAt)}
            </span>
          </div>
          <h1 className="font-display text-3xl leading-none font-bold md:text-[2.4rem]">{c.name}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {num(s.total)} recipients · subject “{readableTags(c.subject)}”
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href={`/outreach?template=${c.id}`} className={buttonVariants({ variant: "outline", size: "lg" })}>
            <Copy /> Reuse campaign
          </Link>
          {canFollowUp ? (
            <Button size="lg" className="font-semibold" onClick={doFollowUp}>
              <Forward /> Send follow-up now · {s.nonResponders}
            </Button>
          ) : null}
        </div>
      </div>

      {s.status === "Scheduled" ? (
        <div className="mb-5 flex items-center gap-3 rounded-sm border-l-4 border-chart-1 bg-card px-4 py-3" suppressHydrationWarning>
          <CalendarClock className="size-5 text-chart-1" />
          <span className="text-sm">
            Scheduled for <b>{when(startAt)}</b>. {s.total} emails are ready and will go out automatically.
          </span>
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col items-center justify-center rounded-sm border border-border bg-card p-5 text-center">
          <div className="eyebrow">Goal · {c.goal ? GOAL_LABEL[c.goal.type] : "none set"}</div>
          <Gauge value={(s.goal?.pct ?? 0) * 100} className="mt-3 w-44" label="Goal progress" />
          <div className="mt-1 text-4xl font-semibold">{s.goal ? `${s.goal.current}/${s.goal.target}` : "–"}</div>
          <div className={cn("mt-1 text-sm font-medium", s.goal && s.goal.pct >= 1 ? "text-good" : "text-muted-foreground")}>
            {s.goal ? (s.goal.pct >= 1 ? "Goal reached" : `${pctText(s.goal.pct)} of the goal`) : "Set a goal when you reuse it"}
          </div>
        </div>

        <Panel title="Funnel" description="Share of delivered emails">
          <BarList
            rows={[
              { label: "Delivered", value: s.delivered, sub: s.bounced ? `${s.bounced} bounced` : undefined },
              { label: "Opened", value: s.opened, sub: pctText(s.openRate) },
              { label: "Replied", value: s.replied, sub: pctText(s.replyRate) },
              { label: "Interested", value: s.positive, sub: pctText(s.positiveRate) },
              { label: "Won as customer", value: s.won, sub: `${s.loads} loads booked` },
            ]}
            max={Math.max(1, s.delivered)}
          />
        </Panel>

        <Panel title="Sequence" description="First email, then automatic follow-ups to people who didn't reply">
          <ol className="space-y-3">
            {s.steps.map((st, i) => {
              const f = i === 0 ? null : c.followUps?.[i - 1]
              const sent = i === 0 ? s.delivered > 0 : (c.followUpsSent ?? 0) >= i
              return (
                <li key={st.step} className="flex gap-3">
                  <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-sm font-display text-sm font-bold", sent ? "bg-asphalt text-safety" : "border border-dashed border-border text-muted-foreground")}>{st.step}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold">{i === 0 ? "First email" : `Follow-up ${i} · after ${f?.afterDays} days`}</div>
                    <div className="truncate text-xs text-muted-foreground">{readableTags((i === 0 ? c.subject : f?.subject) ?? "")}</div>
                    <div className="mt-0.5 text-xs" suppressHydrationWarning>
                      {sent ? (
                        <span><b className="num font-mono">{st.replies}</b> replies from {st.delivered} emails · {pctText(st.replyRate)}</span>
                      ) : (
                        <span className="text-chart-1">Goes out {followUpAt && i === (c.followUpsSent ?? 0) + 1 ? when(followUpAt) : "later"} to people who haven&apos;t replied</span>
                      )}
                    </div>
                  </div>
                </li>
              )
            })}
            {!c.followUps?.length ? <li className="text-xs text-muted-foreground">No follow-ups in this campaign. Past results show follow-ups bring more replies, so add one when you reuse it.</li> : null}
          </ol>
        </Panel>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-chart-2" /> Reply analysis</span>} description={`${s.replied} replies read by the AI · average sentiment ${s.avgSentiment >= 0 ? "+" : ""}${s.avgSentiment.toFixed(2)}`}>
          <StackBar parts={(Object.keys(REPLY_LABEL) as ReplyCategory[]).map((k) => ({ label: REPLY_LABEL[k], value: s.byCategory[k], color: REPLY_COLOR[k] }))} />
          <p className="mt-4 border-t border-border pt-3 text-sm text-muted-foreground">
            <span className="font-semibold text-foreground">AI read: </span>
            {s.replied === 0
              ? "No replies yet. Most replies arrive within 48 hours of each email."
              : `${s.positive} of ${s.replied} replies are positive${topLane && (topLaneCount ?? 0) >= 2 ? `. The lane asked about most is ${topLane} (${topLaneCount} replies)` : ""}. ${s.byCategory.not_now} said “not right now”, so plan a follow-up with them in about 30 days. ${s.byCategory.unsubscribe ? `${s.byCategory.unsubscribe} asked to be removed and were taken off all lists automatically.` : ""}`}
          </p>
        </Panel>
        <Panel title="Who replied" description="Reply rate by region and company type">
          <BarList rows={breakdown((r) => (r.region === "US" ? "United States" : "Europe"))} format={(v) => `${v}%`} />
          <div className="mt-4">
            <BarList
              color="var(--chart-4)"
              rows={breakdown((r) => (r.kind === "broker" ? "Existing brokers" : r.companyType ? LEAD_KIND_LABEL[r.companyType] : undefined))}
              format={(v) => `${v}%`}
            />
          </div>
        </Panel>
      </div>

      <Panel className="mt-5" title="Replies" description="Every reply, with what the AI understood from it" bodyClassName="p-0">
        <div className="flex flex-wrap gap-1 border-b border-border p-2">
          {(["all", ...Object.keys(REPLY_LABEL)] as (ReplyCategory | "all")[]).map((k) => {
            const n = k === "all" ? replies.length : s.byCategory[k]
            if (k !== "all" && !n) return null
            return (
              <button key={k} onClick={() => setFilter(k)} className={cn("flex h-8 items-center gap-2 rounded-sm px-3 text-sm font-medium", filter === k ? "bg-asphalt text-white" : "hover:bg-muted")}>
                {k !== "all" ? <span className="size-2 rounded-[2px]" style={{ background: REPLY_COLOR[k] }} /> : null}
                {k === "all" ? "All replies" : REPLY_LABEL[k]}
                <span className="num font-mono text-xs opacity-70">{n}</span>
              </button>
            )
          })}
        </div>
        {shown.length ? (
          <ul className="divide-y divide-border">
            {shown.map((r) => (
              <li key={r.id} className="grid gap-2 px-4 py-3 md:grid-cols-[240px_minmax(0,1fr)_160px]">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <RegionTag region={r.region} />
                    <span className="truncate text-sm font-semibold">{r.name}</span>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{r.contactName}{r.won ? " · won as customer" : ""}</div>
                </div>
                <p className="text-sm italic">“{r.reply!.text}”</p>
                <div className="flex flex-wrap items-start justify-end gap-2 md:flex-col md:items-end">
                  <ReplyChip category={r.reply!.category} />
                  <SentimentDot value={r.reply!.sentiment} />
                  <span className="text-[0.7rem] text-muted-foreground" suppressHydrationWarning>
                    {r.reply!.step > 1 ? `after follow-up ${r.reply!.step - 1} · ` : ""}{timeAgo(new Date(r.reply!.at).toISOString())}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-8 text-center text-sm text-muted-foreground">{replies.length ? "No replies in this category." : "No replies yet. They'll appear here as they arrive, already tagged by the AI."}</p>
        )}
      </Panel>

      <details className="group mt-5 rounded-sm border border-border bg-card">
        <summary className="cursor-pointer list-none px-4 py-3 font-display text-[1.05rem] font-semibold">
          The email that was sent <span className="font-sans text-sm font-normal text-muted-foreground">· click to show</span>
        </summary>
        <div className="max-w-2xl p-4 pt-0">
          <EmailPreview subject={c.subject} body={c.body} design={c.design} recipient={c.recipients[0]} />
        </div>
      </details>

    </>
  )
}
