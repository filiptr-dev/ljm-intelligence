"use client"

import * as React from "react"
import Link from "next/link"
import { Copy, Lightbulb, Plus, Sparkles } from "lucide-react"
import { buttonVariants } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { analyzeCampaigns } from "@/lib/campaigns/metrics"
import { CAMPAIGN_TYPE_LABEL, REPLY_COLOR, REPLY_LABEL, TONE_LABEL, type Campaign, type ReplyCategory } from "@/lib/campaigns/types"
import { dateShort, num } from "@/lib/format"
import { cn } from "@/lib/utils"
import { GoalBar, pctText, ReplyChip, StatusPill } from "./campaign-bits"
import { readableTags } from "./email-preview"
import { useEngine } from "./engine"
import { BarList, PageHeader, Panel, StackBar, StatTile } from "./ui"

export function CampaignsDashboard({ history }: { history: Campaign[] }) {
  const { campaigns: live } = useEngine()
  const all = React.useMemo(
    () => [...live.filter((c) => c.recipients.length && !c.single), ...history].sort((a, b) => b.createdAt - a.createdAt),
    [live, history],
  )
  const a = React.useMemo(() => analyzeCampaigns(all), [all])
  const t = a.totals
  const summaries = new Map(a.list.map(({ c, s }) => [c.id, s]))
  const quotes = (cats: ReplyCategory[], n: number) => a.allReplies.filter((r) => cats.includes(r.category)).slice(0, n)

  return (
    <>
      <PageHeader
        eyebrow="Campaigns"
        title="Campaign results"
        description="Every campaign you have sent, its goal, and what came back. The AI reads each reply, so you can see which messages win customers and reuse them."
        actions={
          <Link href="/outreach" className={cn(buttonVariants({ size: "lg" }), "font-semibold")}>
            <Plus /> New campaign
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Campaigns" value={all.length} sub={`${a.list.filter(({ s }) => s.status !== "Completed").length} running or scheduled`} />
        <StatTile label="Emails delivered" value={num(t.delivered)} sub={`${pctText(t.opened / (t.delivered || 1))} opened`} />
        <StatTile label="Reply rate" value={pctText(t.replied / (t.delivered || 1))} sub={`${num(t.replied)} replies`} />
        <StatTile label="Interested replies" value={num(t.positive)} sub={`${pctText(t.positive / (t.replied || 1))} of all replies`} />
        <StatTile label="New customers won" value={num(t.won)} sub="Booked a first load" />
        <StatTile label="Loads from campaigns" value={num(t.loads)} sub="Booked after a reply" />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-chart-2" /> What works · AI analysis</span>} description="Learned from all your campaigns and the replies they got" bodyClassName="p-0">
          <ul className="divide-y divide-border">
            {a.insights.map((i) => (
              <li key={i.title} className="flex gap-3 p-4">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-sm bg-chart-1 text-white"><Lightbulb className="size-4" /></span>
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{i.title}</div>
                  <p className="text-sm text-muted-foreground">{i.body}</p>
                </div>
                {i.reuseId ? (
                  <Link href={`/outreach?template=${i.reuseId}`} className={cn(buttonVariants({ variant: "outline", size: "sm" }), "shrink-0 self-center")}>
                    <Copy /> Reuse
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Goals" description="Progress towards each campaign's goal">
          <ul className="space-y-4">
            {a.list.filter(({ s }) => s.goal).slice(0, 7).map(({ c, s }) => (
              <li key={c.id}>
                <div className="mb-1.5 flex items-center gap-2">
                  <Link href={`/campaigns/${c.id}`} className="min-w-0 flex-1 truncate text-sm font-semibold hover:underline">{c.name}</Link>
                  <StatusPill status={s.status} />
                </div>
                <GoalBar goal={s.goal} />
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel className="mt-5" title="All campaigns" description="Click a campaign to see every reply and the full results" bodyClassName="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">Campaign</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Sent to</TableHead>
              <TableHead className="text-right">Opened</TableHead>
              <TableHead className="text-right">Replied</TableHead>
              <TableHead className="text-right">Interested</TableHead>
              <TableHead>Goal</TableHead>
              <TableHead className="pr-4 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {all.map((c) => {
              const s = summaries.get(c.id)!
              return (
                <TableRow key={c.id}>
                  <TableCell className="max-w-[320px] pl-4">
                    <Link href={`/campaigns/${c.id}`} className="flex items-center gap-2 font-semibold hover:underline">
                      {c.auto ? <span className="rounded-[3px] bg-safety px-1.5 text-[0.6rem] font-bold text-asphalt">AUTO</span> : null}
                      <span className="truncate">{c.name}</span>
                    </Link>
                    <div className="text-xs text-muted-foreground" suppressHydrationWarning>
                      {CAMPAIGN_TYPE_LABEL[c.type] ?? c.type}
                      {c.tone ? ` · ${TONE_LABEL[c.tone]}` : ""} · {dateShort(new Date(c.schedule?.at ?? c.createdAt).toISOString())}
                      {c.followUps?.length ? ` · ${c.followUps.length} follow-up${c.followUps.length > 1 ? "s" : ""}` : ""}
                    </div>
                  </TableCell>
                  <TableCell><StatusPill status={s.status} /></TableCell>
                  <TableCell className="num text-right font-mono">{s.total}</TableCell>
                  <TableCell className="num text-right font-mono">{pctText(s.openRate)}</TableCell>
                  <TableCell className="num text-right font-mono">{pctText(s.replyRate)}</TableCell>
                  <TableCell className="num text-right font-mono">{s.positive}</TableCell>
                  <TableCell><GoalBar goal={s.goal} compact /></TableCell>
                  <TableCell className="pr-4 text-right">
                    <div className="flex justify-end gap-1.5">
                      <Link href={`/campaigns/${c.id}`} className={buttonVariants({ variant: "ghost", size: "sm" })}>View</Link>
                      <Link href={`/outreach?template=${c.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}><Copy /> Reuse</Link>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </Panel>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Reply sentiment" description={`${num(a.allReplies.length)} replies, each read and tagged by the AI`}>
          <StackBar parts={(Object.keys(REPLY_LABEL) as ReplyCategory[]).map((k) => ({ label: REPLY_LABEL[k], value: a.byCategory[k], color: REPLY_COLOR[k] }))} />
          <div className="mt-4 grid gap-3 border-t border-border pt-3 sm:grid-cols-2">
            <div>
              <div className="eyebrow mb-1.5">What interested companies say</div>
              <ul className="space-y-2">
                {quotes(["interested", "rates"], 3).map((r, i) => (
                  <li key={i} className="text-sm">
                    <span className="italic">“{r.text}”</span>
                    <div className="text-xs text-muted-foreground">{r.contactName} · {r.name}</div>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="eyebrow mb-1.5">Why others say no</div>
              <ul className="space-y-2">
                {quotes(["not_interested", "not_now"], 3).map((r, i) => (
                  <li key={i} className="text-sm">
                    <div className="mb-0.5"><ReplyChip category={r.category} /></div>
                    <span className="italic">“{r.text}”</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Panel>

        <Panel title="Who responds best" description="Interested replies as a share of delivered emails">
          <div className="eyebrow mb-2">By campaign type</div>
          <BarList color="var(--good)" rows={a.byType.map((g) => ({ label: g.label, value: Math.round(g.positiveRate * 100), sub: `${g.campaigns} campaign${g.campaigns > 1 ? "s" : ""} · ${num(g.delivered)} emails` }))} format={(v) => `${v}%`} />
          <div className="eyebrow mt-5 mb-2">By audience</div>
          <BarList color="var(--good)" rows={a.byAudience.map((g) => ({ label: g.label, value: Math.round(g.positiveRate * 100) }))} format={(v) => `${v}%`} />
        </Panel>

        <Panel title="Best subject lines" description="Ranked by interested replies. Reuse a winner as your next campaign." bodyClassName="p-0">
          <ul className="divide-y divide-border">
            {a.subjects.slice(0, 6).map((x, i) => (
              <li key={x.id} className="flex items-center gap-3 px-4 py-3">
                <span className="w-5 font-mono text-sm font-semibold text-muted-foreground">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold">{readableTags(x.subject)}</div>
                  <div className="truncate text-xs text-muted-foreground">{x.name}</div>
                </div>
                <div className="text-right">
                  <div className="num font-mono text-sm font-semibold">{pctText(x.positiveRate)}</div>
                  <div className="text-[0.65rem] text-muted-foreground">interested</div>
                </div>
                <Link href={`/outreach?template=${x.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}><Copy /> Reuse</Link>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Tone & launch day" description="Reply rate by the tone of the email and the day it went out">
          <div className="eyebrow mb-2">By tone · interested replies</div>
          <BarList rows={a.byTone.map((g) => ({ label: g.label, value: Math.round(g.positiveRate * 100) }))} format={(v) => `${v}%`} />
          <div className="eyebrow mt-5 mb-2">By launch day · all replies</div>
          <BarList rows={a.byDay.map((g) => ({ label: g.label, value: Math.round(g.replyRate * 100) }))} format={(v) => `${v}%`} />
        </Panel>
      </div>
    </>
  )
}
