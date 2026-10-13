import Link from "next/link"
import { PageHeader, Panel } from "@/components/app/ui"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import * as analysis from "@/lib/api/analysis"

/**
 * Broker Insights — human-readable top cards + the raw tables below.
 *
 * The top five cards are the operator's morning read: "who's hot, who's
 * gone quiet, best time to email, slow payers, usual lane rate". Each is
 * one sentence + one action; empty cards hide. The old raw tables are
 * preserved under the collapsible "More details" footer so nothing is
 * lost — this is a reskin on the read side, not a feature cut.
 *
 * Broker whitelist (plan: `list_broker_predictions`) is enforced in the
 * backend repository, so wexinc.com / coxautoinc.com / remotelyplushire.com
 * — any inbound domain that was never pointed at a real Lead — never reach
 * this page.
 */
export default async function IntelligencePage() {
  const data = await analysis.getPredictions()
  const brokers = data.brokers
  const lanes = data.lanes
  const objections = data.objections
  const workload = data.workload
  const thread_age = data.thread_age
  const loss_reasons = data.loss_reasons
  const first_touch = data.first_touch

  const topBrokers = [...brokers].sort((a, b) => (b.win_probability * (1 - b.churn_risk)) - (a.win_probability * (1 - a.churn_risk))).slice(0, 10)
  const slowPayers = brokers.filter((b) => b.is_slow_payer).slice(0, 10)
  const churnWarnings = [...brokers].filter((b) => b.churn_risk >= 0.3).sort((a, b) => b.churn_risk - a.churn_risk).slice(0, 10)
  const bestHours = brokers.filter((b) => b.best_send_hour !== null && b.best_send_hour !== undefined).slice(0, 10)

  // Rolling "best send hour" over the whole book — the per-broker signal
  // is thin, so lead with the aggregate (Tuesday 9 AM Eastern feel).
  const hourCounts = new Map<number, number>()
  for (const b of brokers) {
    if (b.best_send_hour == null) continue
    hourCounts.set(b.best_send_hour, (hourCounts.get(b.best_send_hour) ?? 0) + 1)
  }
  let aggregateHourUtc: number | null = null
  let aggregateHourN = 0
  for (const [h, n] of hourCounts) {
    if (n > aggregateHourN) { aggregateHourUtc = h; aggregateHourN = n }
  }
  const aggregateHourEt = aggregateHourUtc != null ? utcHourToEt(aggregateHourUtc) : null

  const topLane = [...lanes]
    .filter((l) => l.price_p50 != null && l.sample_size >= 3)
    .sort((a, b) => b.sample_size - a.sample_size)[0] || null

  return (
    <>
      <PageHeader
        eyebrow="Analyze"
        title="Broker Insights"
        description={`Nightly predictions over the inbox. ${brokers.length} broker${brokers.length === 1 ? "" : "s"} analyzed, ${lanes.length} lane${lanes.length === 1 ? "" : "s"}.`}
      />

      {brokers.length === 0 && lanes.length === 0 ? (
        <div className="mb-6 rounded-sm border border-border bg-card p-4 text-sm text-muted-foreground">
          Not enough email yet — importing history. Insights appear here once the first inbox sync + nightly analysis complete.
        </div>
      ) : null}

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        {topBrokers.length > 0 ? (
          <Panel title="Who's running hot right now" description="Top shippers you're most likely to book this week. Call these first.">
            <ul className="space-y-2 text-sm">
              {topBrokers.slice(0, 5).map((b) => (
                <li key={b.broker_domain} className="flex items-center justify-between gap-3">
                  <span className="truncate">
                    {b.lead_id ? (
                      <Link href={`/brokers/${b.lead_id}`} className="underline">{b.broker_name || b.broker_domain}</Link>
                    ) : (
                      <span className="text-muted-foreground">{b.broker_name || b.broker_domain}</span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">{Math.round(b.win_probability * 100)}% win · health {b.health_score}</span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        {churnWarnings.length > 0 ? (
          <Panel title="Who's gone quiet" description="A short follow-up usually gets a reply.">
            <ul className="space-y-2 text-sm">
              {churnWarnings.slice(0, 5).map((b) => (
                <li key={b.broker_domain} className="flex items-center justify-between gap-3">
                  <span className="truncate">
                    {b.lead_id ? (
                      <Link href={`/brokers/${b.lead_id}`} className="underline">{b.broker_name || b.broker_domain}</Link>
                    ) : (
                      <span className="text-muted-foreground">{b.broker_name || b.broker_domain}</span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">{Math.round(b.churn_risk * 100)}% churn risk</span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        {aggregateHourEt != null ? (
          <Panel title="Best time to email" description="Across your inbox, this hour gets you the fastest reply.">
            <p className="text-sm">
              Try around <strong>{formatEtHour(aggregateHourEt)} Eastern</strong>. {aggregateHourN >= 3 ? "" : <span className="text-muted-foreground">(thin signal — only {aggregateHourN} broker{aggregateHourN === 1 ? "" : "s"} so far)</span>}
            </p>
          </Panel>
        ) : null}

        {slowPayers.length > 0 ? (
          <Panel title="Watch out — slow payers" description="These brokers have asked about payment timing more than once. Confirm terms before booking.">
            <ul className="space-y-2 text-sm">
              {slowPayers.slice(0, 5).map((b) => (
                <li key={b.broker_domain} className="truncate">
                  {b.lead_id ? (
                    <Link href={`/brokers/${b.lead_id}`} className="underline">{b.broker_name || b.broker_domain}</Link>
                  ) : (
                    <span className="text-muted-foreground">{b.broker_name || b.broker_domain}</span>
                  )}
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        {topLane ? (
          <Panel title="Usual rate on this lane" description="Based on past accepted quotes.">
            <p className="text-sm">
              <strong>{topLane.origin} → {topLane.dest}</strong>{topLane.equipment ? ` · ${topLane.equipment}` : ""} usually pays <strong>${Math.round(topLane.price_p50 || 0).toLocaleString()}</strong>
              {topLane.price_p90 ? <>, up to <strong>${Math.round(topLane.price_p90).toLocaleString()}</strong></> : null}.
              <span className="text-muted-foreground"> ({topLane.sample_size} quote{topLane.sample_size === 1 ? "" : "s"})</span>
            </p>
          </Panel>
        ) : null}
      </div>

      <details className="group mb-6">
        <summary className="cursor-pointer select-none text-sm text-muted-foreground underline">More details (raw tables)</summary>
        <div className="mt-4">

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Win probability" description="Likelihood this broker books us on their next load.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Win prob</TableHead><TableHead>Health</TableHead></TableRow></TableHeader>
            <TableBody>
              {topBrokers.map((b) => (
                <TableRow key={b.broker_domain}>
                  <TableCell className="font-mono text-xs">{b.broker_name || b.broker_domain}</TableCell>
                  <TableCell>{(b.win_probability * 100).toFixed(0)}%</TableCell>
                  <TableCell>{b.health_score}</TableCell>
                </TableRow>
              ))}
              {topBrokers.length === 0 ? <TableRow><TableCell colSpan={3} className="text-sm text-muted-foreground">No broker predictions yet — the nightly analysis runs after your first inbox sync.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Price-to-win by lane" description="p50 / p75 / p90 of accepted rates per lane.">
          <Table>
            <TableHeader><TableRow><TableHead>Lane</TableHead><TableHead>p50</TableHead><TableHead>p75</TableHead><TableHead>p90</TableHead><TableHead>N</TableHead></TableRow></TableHeader>
            <TableBody>
              {lanes.slice(0, 15).map((l, i) => (
                <TableRow key={`${l.origin}-${l.dest}-${i}`}>
                  <TableCell className="font-mono text-xs">{l.origin} → {l.dest}{l.equipment ? ` · ${l.equipment}` : ""}</TableCell>
                  <TableCell>{l.price_p50 ? `$${l.price_p50.toLocaleString()}` : "—"}</TableCell>
                  <TableCell>{l.price_p75 ? `$${l.price_p75.toLocaleString()}` : "—"}</TableCell>
                  <TableCell>{l.price_p90 ? `$${l.price_p90.toLocaleString()}` : "—"}</TableCell>
                  <TableCell>{l.sample_size}</TableCell>
                </TableRow>
              ))}
              {lanes.length === 0 ? <TableRow><TableCell colSpan={5} className="text-sm text-muted-foreground">No lane prices yet — we need at least a few quoted threads before the first p50/p75/p90 lands.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Broker churn warning" description="Volume drop vs prior 30 days.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Risk</TableHead></TableRow></TableHeader>
            <TableBody>
              {churnWarnings.map((b) => (
                <TableRow key={b.broker_domain}>
                  <TableCell className="font-mono text-xs">{b.broker_name || b.broker_domain}</TableCell>
                  <TableCell>{(b.churn_risk * 100).toFixed(0)}%</TableCell>
                </TableRow>
              ))}
              {churnWarnings.length === 0 ? <TableRow><TableCell colSpan={2} className="text-sm text-muted-foreground">No churn warnings right now.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Best send time" description="Hour-of-day with the highest reply ratio per broker.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Hour (UTC)</TableHead></TableRow></TableHeader>
            <TableBody>
              {bestHours.map((b) => (
                <TableRow key={b.broker_domain}>
                  <TableCell className="font-mono text-xs">{b.broker_name || b.broker_domain}</TableCell>
                  <TableCell>{String(b.best_send_hour).padStart(2, "0")}:00</TableCell>
                </TableRow>
              ))}
              {bestHours.length === 0 ? <TableRow><TableCell colSpan={2} className="text-sm text-muted-foreground">No send-time patterns yet — we learn this from reply timing once your inbox is analyzed.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Slow payers" description="Brokers whose threads show repeated payment-intent messages.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead></TableRow></TableHeader>
            <TableBody>
              {slowPayers.map((b) => (
                <TableRow key={b.broker_domain}><TableCell className="font-mono text-xs">{b.broker_name || b.broker_domain}</TableCell></TableRow>
              ))}
              {slowPayers.length === 0 ? <TableRow><TableCell className="text-sm text-muted-foreground">No slow payers flagged.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Reply-speed impact" description="How much faster brokers reply when we reply fast.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Lift</TableHead></TableRow></TableHeader>
            <TableBody>
              {brokers.slice(0, 10).map((b) => (
                <TableRow key={b.broker_domain}>
                  <TableCell className="font-mono text-xs">{b.broker_name || b.broker_domain}</TableCell>
                  <TableCell>{b.reply_speed_lift.toFixed(2)}×</TableCell>
                </TableRow>
              ))}
              {brokers.length === 0 ? <TableRow><TableCell colSpan={2} className="text-sm text-muted-foreground">No reply-speed lift yet — needs at least one full back-and-forth per broker.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Objection clusters" description="What brokers push back on, bucketed per broker.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Objection</TableHead><TableHead>Count</TableHead></TableRow></TableHeader>
            <TableBody>
              {objections.slice(0, 20).map((o, i) => (
                <TableRow key={`${o.broker_domain}-${o.label}-${i}`}>
                  <TableCell className="font-mono text-xs">{o.broker_domain}</TableCell>
                  <TableCell>{o.label}</TableCell>
                  <TableCell>{o.count}</TableCell>
                </TableRow>
              ))}
              {objections.length === 0 ? <TableRow><TableCell colSpan={3} className="text-sm text-muted-foreground">No objections clustered yet — the nightly analysis buckets these from inbound replies.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Loss-reason tags" description="Why dropped quotes get dropped, across the whole book.">
          <Table>
            <TableHeader><TableRow><TableHead>Reason</TableHead><TableHead>Count</TableHead></TableRow></TableHeader>
            <TableBody>
              {loss_reasons.map((l) => (
                <TableRow key={l.reason}><TableCell>{l.reason}</TableCell><TableCell>{l.count}</TableCell></TableRow>
              ))}
              {loss_reasons.length === 0 ? <TableRow><TableCell colSpan={2} className="text-sm text-muted-foreground">No loss reasons tagged yet.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="Thread age before answer" description="How long inbound threads sit before someone replies, per intent.">
          <Table>
            <TableHeader><TableRow><TableHead>Intent</TableHead><TableHead>Median</TableHead><TableHead>p90</TableHead><TableHead>N</TableHead></TableRow></TableHeader>
            <TableBody>
              {thread_age.map((t) => (
                <TableRow key={t.intent}>
                  <TableCell>{t.intent}</TableCell>
                  <TableCell>{fmtMinutes(t.median_minutes)}</TableCell>
                  <TableCell>{fmtMinutes(t.p90_minutes)}</TableCell>
                  <TableCell>{t.count}</TableCell>
                </TableRow>
              ))}
              {thread_age.length === 0 ? <TableRow><TableCell colSpan={4} className="text-sm text-muted-foreground">No thread timings yet — we measure this across your inbound threads.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>

        <Panel title="First-touch → first-load latency" description="Days from first contact to first load offer, per newly contacted broker.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Days</TableHead></TableRow></TableHeader>
            <TableBody>
              {first_touch.slice(0, 20).map((f) => (
                <TableRow key={f.broker_domain}>
                  <TableCell className="font-mono text-xs">{f.broker_domain}</TableCell>
                  <TableCell>{f.latency_days !== null && f.latency_days !== undefined ? f.latency_days.toFixed(1) : "—"}</TableCell>
                </TableRow>
              ))}
              {first_touch.length === 0 ? <TableRow><TableCell colSpan={2} className="text-sm text-muted-foreground">No first-touch latencies yet — needs at least one broker who moved from first contact to first load.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>
      </div>

      <div className="mt-5">
        <Panel title="Staff workload heatmap" description="When our team is actually sending (hours × day of week).">
          {workload.length === 0 ? <p className="text-sm text-muted-foreground">No sends recorded yet — this heatmap fills in once your team starts sending.</p> : null}
          <WorkloadHeatmap cells={workload} />
        </Panel>
      </div>

      <div className="mt-5">
        <Panel title="Lookalike brokers" description="Peers that work the same lanes — mirror-sell candidates.">
          <Table>
            <TableHeader><TableRow><TableHead>Broker</TableHead><TableHead>Health</TableHead><TableHead>Lookalike peers</TableHead></TableRow></TableHeader>
            <TableBody>
              {brokers.slice(0, 10).map((b) => (
                <LookalikesRow key={b.broker_domain} brokerDomain={b.broker_domain} brokerName={b.broker_name} health={b.health_score} />
              ))}
              {brokers.length === 0 ? <TableRow><TableCell colSpan={3} className="text-sm text-muted-foreground">No broker peers yet — lookalikes fill in after the first nightly analysis.</TableCell></TableRow> : null}
            </TableBody>
          </Table>
        </Panel>
      </div>
        </div>
      </details>
    </>
  )
}

// --- helpers ---

// Convert a UTC hour-of-day to the equivalent ET hour, using the current
// America/New_York offset. Simple approach: build a Date at today's UTC
// hour=h, format in ET, parse back out the hour. Avoids pulling in a
// dateutil dependency; good enough for a "9 AM Eastern" copy line.
function utcHourToEt(hourUtc: number): number {
  const d = new Date()
  d.setUTCHours(hourUtc, 0, 0, 0)
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    hour12: false,
    timeZone: "America/New_York",
  }).formatToParts(d)
  const h = parts.find((p) => p.type === "hour")?.value ?? "0"
  return parseInt(h, 10) % 24
}

function formatEtHour(hourEt: number): string {
  if (hourEt === 0) return "midnight"
  if (hourEt === 12) return "noon"
  const h12 = hourEt > 12 ? hourEt - 12 : hourEt
  const ampm = hourEt >= 12 ? "PM" : "AM"
  return `${h12} ${ampm}`
}

async function LookalikesRow({ brokerDomain, brokerName, health }: { brokerDomain: string; brokerName: string | null | undefined; health: number }) {
  const peers = await analysis.getLookalikes(brokerDomain, 5).catch(() => [])
  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{brokerName || brokerDomain}</TableCell>
      <TableCell>{health}</TableCell>
      <TableCell className="text-xs font-mono">
        {peers.length ? peers.map((p) => `${p.peer_domain} (${p.score.toFixed(2)})`).join(", ") : <span className="text-muted-foreground">—</span>}
      </TableCell>
    </TableRow>
  )
}

function fmtMinutes(m: number): string {
  if (m < 60) return `${m}m`
  if (m < 1440) return `${Math.round(m / 60)}h`
  return `${Math.round(m / 1440)}d`
}

function WorkloadHeatmap({ cells }: { cells: analysis.WorkloadCell[] }) {
  const grid: number[][] = Array.from({ length: 7 }, () => Array(24).fill(0))
  let max = 0
  for (const c of cells) {
    if (c.day < 0 || c.day > 6 || c.hour < 0 || c.hour > 23) continue
    grid[c.day][c.hour] = c.count
    if (c.count > max) max = c.count
  }
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
  return (
    <div className="overflow-x-auto">
      <table className="font-mono text-[0.65rem]">
        <thead>
          <tr>
            <th className="px-1 text-left">·</th>
            {Array.from({ length: 24 }, (_, h) => (
              <th key={h} className="px-1 text-left">{String(h).padStart(2, "0")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, d) => (
            <tr key={d}>
              <td className="px-1 text-right pr-2">{days[d]}</td>
              {row.map((v, h) => {
                const alpha = max > 0 ? v / max : 0
                const bg = `rgba(42,124,222,${alpha})`
                return <td key={h} className="h-5 w-5 border border-border/40" style={{ backgroundColor: bg }} title={`${days[d]} ${h}:00 — ${v}`} />
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
