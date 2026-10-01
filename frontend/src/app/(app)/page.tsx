import Link from "next/link"
import { ArrowRight, Lightbulb, Sparkles, TriangleAlert, Zap } from "lucide-react"
import { LiveFeed, MonitoringHero } from "@/components/app/live-feed"
import { BarList, PageHeader, Panel, SEGMENT_COLOR, StackBar, StatTile } from "@/components/app/ui"
import { OutcomeColumns } from "@/components/charts/charts"
import { buttonVariants } from "@/components/ui/button"
import { SOURCE_LIST } from "@/lib/data/leads"
import { getStore } from "@/lib/data/store"
import { money, pct } from "@/lib/format"
import { cn } from "@/lib/utils"

const TONE = {
  action: { icon: Zap, cls: "bg-safety text-asphalt" },
  insight: { icon: Lightbulb, cls: "bg-chart-1 text-white" },
  warning: { icon: TriangleAlert, cls: "bg-bad text-white" },
}

export default async function OverviewPage() {
  const s = await getStore()
  const k = s.kpis
  const bookedDelta = (k.booked90 - k.bookedPrev90) / (k.bookedPrev90 || 1)
  const winDelta = k.winRate90 - k.winRatePrev90
  const sources = SOURCE_LIST.US.length + SOURCE_LIST.EU.length

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="Freight desk"
        description={`Your broker relationships, analysed from ${k.emails.toLocaleString("en-US")} emails with ${k.brokers} brokers over the last 18 months.`}
        actions={
          <>
            <Link href="/intelligence" className={buttonVariants({ variant: "outline" })}>Open intelligence</Link>
            <Link href="/outreach" className={cn(buttonVariants(), "font-semibold")}>New outreach <ArrowRight /></Link>
          </>
        }
      />

      <MonitoringHero sources={sources} />

      <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Loads booked · 90 days" value={k.booked90} delta={bookedDelta} deltaLabel={`${bookedDelta >= 0 ? "+" : ""}${pct(bookedDelta)} vs previous 90 days`} />
        <StatTile label="Win rate · 90 days" value={pct(k.winRate90)} delta={winDelta} deltaLabel={`${winDelta >= 0 ? "+" : ""}${Math.round(winDelta * 100)} pts vs previous 90 days`} />
        <StatTile label="Revenue · 90 days" value={money(k.revenueUSD90, "US", true)} sub={`${money(k.revenueUSD, "US", true)} over 18 months`} />
        <StatTile label="Median quote reply time" value={`${Math.round(k.medianResponseMin)} min`} sub="Time from load offer to your quote" />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Panel
          title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-chart-2" /> AI insights</span>}
          description="Found by semantic analysis of your email history. Updated after every sync."
          bodyClassName="p-0"
        >
          <ul className="divide-y divide-border">
            {s.highlights.map((h) => {
              const t = TONE[h.tone]
              return (
                <li key={h.id} className="flex gap-4 p-4">
                  <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-sm", t.cls)}>
                    <t.icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="font-semibold">{h.title}</h3>
                    <p className="mt-0.5 text-sm text-muted-foreground">{h.body}</p>
                    {h.brokers.length ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {h.brokers.map((b) => (
                          <span key={b} className="rounded-sm bg-muted px-1.5 py-0.5 text-xs">{b}</span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <Link href={h.cta.href} className={cn(buttonVariants({ variant: "outline", size: "sm" }), "hidden shrink-0 self-center md:inline-flex")}>
                    {h.cta.label}
                  </Link>
                </li>
              )
            })}
          </ul>
        </Panel>

        <Panel
          title="Live activity"
          description="Crawler, verification and outreach events"
          action={<Link href="/leads" className="text-xs font-semibold text-chart-1 hover:underline">Lead Finder →</Link>}
          bodyClassName="py-0"
        >
          <LiveFeed limit={11} />
        </Panel>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel title="Loads booked vs rejected" description="Every load conversation, by month">
          <OutcomeColumns data={s.monthly} />
        </Panel>
        <div className="grid gap-5">
          <Panel title="Broker segments" description="k-means clustering on volume, win rate, pricing, recency and tone">
            <StackBar parts={s.segments.map((g) => ({ label: g.segment, value: g.count, color: SEGMENT_COLOR[g.segment] }))} />
          </Panel>
          <Panel title="Top partners" description="Loads booked, all time" action={<Link href="/brokers" className="text-xs font-semibold text-chart-1 hover:underline">All brokers →</Link>}>
            <BarList
              rows={s.topPartners.slice(0, 5).map((b) => ({
                label: <Link href={`/brokers/${b.id}`} className="hover:underline">{b.name}</Link>,
                value: b.booked,
                hint: `${b.booked} loads · ${money(b.revenue, b.region)}`,
              }))}
              format={(v) => `${v} loads`}
            />
          </Panel>
        </div>
      </div>
    </>
  )
}
