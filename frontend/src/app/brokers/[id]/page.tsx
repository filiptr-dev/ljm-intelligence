import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, Mail, Phone, Send, Sparkles, TriangleAlert } from "lucide-react"
import { IntentBadge, SentimentDot } from "@/components/app/intent"
import { BarList, Panel, RegionTag, SegmentBadge, StatTile } from "@/components/app/ui"
import { Gauge, Plate } from "@/components/brand/marks"
import { ActivityColumns } from "@/components/charts/charts"
import { buttonVariants } from "@/components/ui/button"
import { getAI } from "@/lib/ai"
import { REASON_LABELS } from "@/lib/analytics"
import type { RejectionReason } from "@/lib/ai/types"
import { getStore } from "@/lib/data/store"
import { DEMO_NOW } from "@/lib/data/types"
import { dateTime, money, pct, perUnit, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

export default async function BrokerPage({ params }: PageProps<"/brokers/[id]">) {
  const { id } = await params
  const s = await getStore()
  const b = s.brokerMap.get(id)
  const st = s.statsById.get(id)
  if (!b || !st) notFound()

  const summary = await getAI().summarizeBroker({
    name: b.name, region: b.region, segment: st.segment, threads: st.threads, booked: st.booked,
    rejected: st.rejected, winRate: st.winRate, fleetWinRate: s.kpis.winRate, revenue: st.revenue,
    topLane: st.topLane, topReason: st.topReason, daysSinceLast: st.daysSinceLast,
    avgResponseMin: st.avgResponseMin, sentiment: st.sentiment, paymentIssues: st.paymentIssues, complaints: st.complaints,
  })

  const threads = s.threads.filter((t) => t.brokerId === id)
  const months = Array.from({ length: 12 }, (_, i) => {
    const d = new Date(DEMO_NOW)
    d.setUTCDate(1)
    d.setUTCMonth(d.getUTCMonth() - 11 + i)
    return d.toISOString().slice(0, 7)
  })
  const activity = months.map((m) => ({
    month: m,
    booked: threads.filter((t) => t.start.startsWith(m) && t.outcome === "booked").length,
    rejected: threads.filter((t) => t.start.startsWith(m) && t.outcome === "rejected").length,
  }))
  const emails = s.emails.filter((e) => e.brokerId === id).reverse()
  const campaign = summary.nextAction.campaign === "none" ? "dedicated_lane" : summary.nextAction.campaign
  const outreachHref = `/outreach?audience=existing&ids=${id}&campaign=${campaign}`

  return (
    <>
      <Link href="/brokers" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All brokers
      </Link>
      <div className="mb-5 flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <RegionTag region={b.region} />
            <Plate region={b.region} value={b.registration.replace(/^[A-Z]{2}(?=\d)/, "")} country={b.country} />
            <SegmentBadge segment={st.segment} />
            <span className="text-sm text-muted-foreground">{b.size} · {b.equipment.join(", ")}</span>
          </div>
          <h1 className="font-display text-3xl leading-none font-bold md:text-[2.4rem]">{b.name}</h1>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted-foreground">
            <span>{b.hq}</span>
            <span className="font-medium text-foreground">{b.contact.name} · {b.contact.title}</span>
            <span className="inline-flex items-center gap-1"><Mail className="size-3.5" /> {b.contact.email}</span>
            <span className="inline-flex items-center gap-1"><Phone className="size-3.5" /> {b.contact.phone}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href={`/messages?to=${id}`} className={cn(buttonVariants({ variant: "outline", size: "lg" }), "font-semibold")}>
            <Mail /> Email {b.contact.name.split(" ")[0]}
          </Link>
          <Link href={outreachHref} className={cn(buttonVariants({ size: "lg" }), "font-semibold")}>
            <Send /> {summary.nextAction.label}
          </Link>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
        <div className="flex flex-col items-center justify-center rounded-sm border border-border bg-card p-5 text-center">
          <div className="eyebrow">Relationship health</div>
          <Gauge value={st.health} className="mt-3 w-44" label="Relationship health" />
          <div className="mt-1 text-4xl font-semibold">{st.health}</div>
          <div className={cn("mt-1 text-sm font-medium", st.healthDelta > 0 ? "text-good" : st.healthDelta < 0 ? "text-bad" : "text-muted-foreground")}>
            {st.healthDelta === 0 ? "Stable vs 90 days ago" : `${st.healthDelta > 0 ? "▲" : "▼"} ${Math.abs(st.healthDelta)} vs 90 days ago`}
          </div>
        </div>
        <Panel title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-chart-2" /> AI summary · {summary.headline}</span>}>
          <p className="text-[0.95rem] leading-relaxed">{summary.summary}</p>
          {summary.risks.length ? (
            <ul className="mt-3 space-y-1.5">
              {summary.risks.map((r) => (
                <li key={r} className="flex items-start gap-2 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" /> {r}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-sm border-l-4 border-safety bg-accent px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="eyebrow text-foreground">Recommended next step</div>
              <div className="font-semibold">{summary.nextAction.label}</div>
              <div className="text-sm text-muted-foreground">{summary.nextAction.detail}</div>
            </div>
            <Link href={`/emails/compose?broker=${id}`} className={buttonVariants({ variant: "outline" })}>Draft email</Link>
          </div>
        </Panel>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Loads booked" value={st.booked} sub={`${st.threads} load conversations`} />
        <StatTile label="Win rate" value={st.booked + st.rejected ? pct(st.winRate) : "–"} delta={st.winRate - s.kpis.winRate} deltaLabel={`${Math.round((st.winRate - s.kpis.winRate) * 100)} pts vs all brokers`} />
        <StatTile label="Revenue" value={money(st.revenue, b.region, true)} sub="All booked loads" />
        <StatTile label="Avg booked rate" value={st.avgPerUnit ? perUnit(st.avgPerUnit, b.region) : "–"} sub={Math.abs(st.rateIndex - 1) < 0.015 ? "Their offers are in line with market" : `Their offers are ${pct(Math.abs(st.rateIndex - 1))} ${st.rateIndex >= 1 ? "above" : "below"} market`} />
        <StatTile label="Your reply time" value={`${Math.round(st.avgResponseMin)} min`} sub="Average, offer to quote" />
        <StatTile label="Last contact" value={timeAgo(st.lastContact, DEMO_NOW.getTime())} sub={`First email ${timeAgo(st.firstContact, DEMO_NOW.getTime())}`} />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Panel title="Activity" description="Load conversations per month, last 12 months">
          <ActivityColumns data={activity} />
        </Panel>
        <Panel title="Why they said no" description={`${st.rejected} rejected quotes`}>
          {st.rejected ? (
            <BarList
              color="var(--chart-3)"
              rows={(Object.entries(st.reasons) as [RejectionReason, number][])
                .sort((a, b) => b[1] - a[1])
                .map(([r, v]) => ({ label: REASON_LABELS[r], value: v }))}
            />
          ) : (
            <p className="text-sm text-muted-foreground">No rejections yet.</p>
          )}
          {st.topLane ? (
            <div className="mt-5 border-t border-border pt-3 text-sm">
              <div className="eyebrow">Main lane</div>
              <div className="mt-0.5 font-semibold">{st.topLane}</div>
            </div>
          ) : null}
        </Panel>
      </div>

      <Panel className="mt-5" title="Email timeline" description={`${emails.length} emails, each tagged by the AI. Click one to read it.`} bodyClassName="p-0">
        <ol className="divide-y divide-border">
          {emails.map((e) => {
            const ins = s.insightMap.get(e.id)!
            return (
              <li key={e.id}>
                <details className="group">
                  <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 hover:bg-muted/60">
                    <span className={cn("w-10 shrink-0 font-mono text-[0.65rem] font-semibold", e.direction === "in" ? "text-chart-1" : "text-muted-foreground")}>
                      {e.direction === "in" ? "IN" : "OUT"}
                    </span>
                    <IntentBadge intent={ins.intent} />
                    <span className="min-w-0 flex-1 truncate text-sm">{e.subject}</span>
                    {ins.rate ? <span className="num font-mono text-xs">{money(ins.rate, b.region)}</span> : null}
                    <SentimentDot value={ins.sentiment} />
                    <span className="w-28 text-right text-xs text-muted-foreground">{dateTime(e.sentAt)}</span>
                  </summary>
                  <div className="grid gap-4 border-t border-dashed border-border bg-background px-4 py-3 md:grid-cols-[minmax(0,1fr)_260px]">
                    <pre className="font-sans text-sm whitespace-pre-wrap">{e.body}</pre>
                    <dl className="space-y-1.5 rounded-sm border border-border bg-card p-3 text-xs">
                      <div className="eyebrow mb-1">AI extraction</div>
                      <Row k="Intent" v={<IntentBadge intent={ins.intent} />} />
                      <Row k="Confidence" v={pct(ins.confidence)} />
                      <Row k="Sentiment" v={ins.sentiment.toFixed(2)} />
                      {ins.lane ? <Row k="Lane" v={`${ins.lane.origin} → ${ins.lane.destination}`} /> : null}
                      {ins.equipment ? <Row k="Equipment" v={ins.equipment} /> : null}
                      {ins.rate ? <Row k="Rate" v={money(ins.rate, b.region)} /> : null}
                      {ins.perUnit ? <Row k="Per unit" v={perUnit(ins.perUnit, b.region)} /> : null}
                      {ins.rejectionReason ? <Row k="Reason" v={REASON_LABELS[ins.rejectionReason]} /> : null}
                      {ins.evidence ? <Row k="Evidence" v={<span className="bg-accent px-1">“{ins.evidence}”</span>} /> : null}
                    </dl>
                  </div>
                </details>
              </li>
            )
          })}
        </ol>
      </Panel>
    </>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="text-right font-medium">{v}</dd>
    </div>
  )
}
