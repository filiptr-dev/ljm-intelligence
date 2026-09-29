import Link from "next/link"
import { Brain, Database, Layers, ScanText } from "lucide-react"
import { RerunButton } from "@/components/app/rerun-button"
import { BarList, PageHeader, Panel, RegionTag, SEGMENT_COLOR } from "@/components/app/ui"
import { RateLine, SpeedColumns, WinRateLine } from "@/components/charts/charts"
import { Heatmap } from "@/components/charts/heatmap"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { REASON_LABELS } from "@/lib/analytics"
import { getStore } from "@/lib/data/store"
import { money, num, pct, perUnit } from "@/lib/format"

const SEGMENT_NOTE: Record<string, string> = {
  "Core partners": "High volume, high win rate, still active. Protect these accounts.",
  Growing: "New or recently active, and volume is rising fast.",
  "Price shoppers": "Send lots of loads but mostly say no on price.",
  Dormant: "Used to book regularly, silent for months.",
  Occasional: "Too little history to profile, so they go on the automated list.",
}

export default async function IntelligencePage() {
  const s = await getStore()
  const avgConf = s.insights.reduce((a, i) => a + i.confidence, 0) / s.insights.length
  const extracted = s.insights.filter((i) => i.rate || i.lane || i.rejectionReason).length
  const intents = new Set(s.insights.map((i) => i.intent)).size

  const steps = [
    { icon: Database, label: "Emails ingested", value: num(s.kpis.emails), sub: `${s.kpis.threads.toLocaleString("en-US")} load conversations` },
    { icon: ScanText, label: "Classified by intent", value: `${intents} intents`, sub: `${pct(avgConf)} average confidence` },
    { icon: Brain, label: "Entities extracted", value: num(extracted), sub: "Rates, lanes, equipment, reasons" },
    { icon: Layers, label: "Brokers segmented", value: `${s.segments.length} segments`, sub: "k-means on 6 behaviour features" },
  ]

  return (
    <>
      <PageHeader
        eyebrow="Broker intelligence"
        title="What your inbox says"
        description={`Every email between your dispatch team and ${s.kpis.brokers} brokers was read by the AI, tagged and turned into the numbers below.`}
        actions={<RerunButton />}
      />

      <ol className="mb-5 grid gap-px overflow-hidden rounded-sm border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((st, i) => (
          <li key={st.label} className="flex items-start gap-3 bg-card p-4">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-asphalt text-safety">
              <st.icon className="size-4" />
            </span>
            <div className="min-w-0">
              <div className="eyebrow">Step {i + 1} · {st.label}</div>
              <div className="mt-0.5 text-xl font-semibold">{st.value}</div>
              <div className="text-xs text-muted-foreground">{st.sub}</div>
            </div>
          </li>
        ))}
      </ol>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Who you work with most" description="Loads booked, all time">
          <BarList
            rows={s.topPartners.map((b) => ({
              label: (
                <Link href={`/brokers/${b.id}`} className="flex items-center gap-2 hover:underline">
                  <RegionTag region={b.region} /> <span className="truncate">{b.name}</span>
                </Link>
              ),
              value: b.booked,
              sub: `${money(b.revenue, b.region)} revenue · ${pct(b.winRate)} win rate`,
            }))}
            format={(v) => `${v} loads`}
          />
        </Panel>
        <Panel title="Who rejects you most" description="Quotes the broker turned down, with the main reason the AI found">
          <BarList
            color="var(--chart-3)"
            rows={s.topRejectors.map((b) => ({
              label: (
                <Link href={`/brokers/${b.id}`} className="flex items-center gap-2 hover:underline">
                  <RegionTag region={b.region} /> <span className="truncate">{b.name}</span>
                </Link>
              ),
              value: b.rejected,
              sub: `${pct(b.rejected / (b.rejected + b.booked))} rejection rate · mostly “${b.topReason ? REASON_LABELS[b.topReason] : "n/a"}”`,
            }))}
            format={(v) => `${v} rejected`}
          />
        </Panel>

        <Panel title="Why brokers say no" description={`${s.kpis.rejected} rejections, by the reason extracted from the email text`}>
          <BarList
            color="var(--chart-3)"
            rows={s.reasons.map((r) => ({ label: r.label, value: r.count, sub: `${pct(r.share)} of rejections` }))}
            format={(v) => String(v)}
          />
          <p className="mt-4 border-t border-border pt-3 text-sm text-muted-foreground">
            <span className="font-semibold text-foreground">AI read: </span>
            {pct(s.reasons[0].share)} of lost loads come down to <b className="text-foreground">{s.reasons[0].label.toLowerCase()}</b>.
            {" "}Losing to other carriers is the second biggest problem, and it clusters in brokers that later went dormant.
          </p>
        </Panel>

        <Panel id="speed" title="Reply speed decides the load" description="Win rate by how fast your dispatcher sent the quote">
          <SpeedColumns data={s.responseBuckets} />
          <p className="mt-2 text-sm text-muted-foreground">
            <span className="font-semibold text-foreground">AI read: </span>
            quotes sent in under 30 minutes win {pct(s.responseBuckets[0].winRate)}, compared with {pct(s.responseBuckets[3].winRate)} after two hours. Auto-drafted quotes would put most replies in the fastest bucket.
          </p>
        </Panel>

        <Panel id="timing" title="When brokers answer" description="Reply rate to your “truck available” emails, by weekday and send time">
          <Heatmap rows={s.heatmap} />
        </Panel>

        <Panel title="Win rate trend" description="Booked ÷ (booked + rejected), by month">
          <WinRateLine data={s.monthly} />
        </Panel>

        <Panel title="Booked rate" description="Average rate per mile on booked loads">
          <RateLine data={s.monthly} dataKey="usdPerMile" label="$/mi" unit="$" />
        </Panel>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Panel title="Broker segments" description="Clusters found by k-means. Click a segment to see its brokers." bodyClassName="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Segment</TableHead>
                <TableHead className="text-right">Brokers</TableHead>
                <TableHead className="text-right">Win rate</TableHead>
                <TableHead className="text-right">Loads</TableHead>
                <TableHead className="pr-4 text-right">Last contact</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {s.segments.map((g) => (
                <TableRow key={g.segment}>
                  <TableCell className="pl-4 whitespace-normal">
                    <Link href={`/brokers?segment=${encodeURIComponent(g.segment)}`} className="flex items-center gap-2 font-semibold hover:underline">
                      <span className="size-2.5 rounded-[2px]" style={{ background: SEGMENT_COLOR[g.segment] }} />
                      {g.segment}
                    </Link>
                    <div className="mt-0.5 text-xs text-muted-foreground">{SEGMENT_NOTE[g.segment]}</div>
                  </TableCell>
                  <TableCell className="num text-right font-mono">{g.count}</TableCell>
                  <TableCell className="num text-right font-mono">{pct(g.winRate)}</TableCell>
                  <TableCell className="num text-right font-mono">{g.booked}</TableCell>
                  <TableCell className="num pr-4 text-right font-mono">{Math.round(g.avgDaysSince)}d ago</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Top lanes" description="Lanes where you book the most loads" bodyClassName="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Lane</TableHead>
                <TableHead className="text-right">Loads</TableHead>
                <TableHead className="text-right">Win rate</TableHead>
                <TableHead className="pr-4 text-right">Avg rate</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {s.lanes.map((l) => (
                <TableRow key={l.lane}>
                  <TableCell className="pl-4">
                    <span className="flex items-center gap-2"><RegionTag region={l.region} /> {l.lane}</span>
                  </TableCell>
                  <TableCell className="num text-right font-mono">{l.loads}</TableCell>
                  <TableCell className="num text-right font-mono">{pct(l.winRate)}</TableCell>
                  <TableCell className="num pr-4 text-right font-mono">{perUnit(l.avgPerUnit, l.region)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      </div>
    </>
  )
}
