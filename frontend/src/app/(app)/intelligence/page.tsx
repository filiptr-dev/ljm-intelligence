import { PageHeader, Panel } from "@/components/app/ui"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import * as analysis from "@/lib/api/analysis"

/**
 * Intelligence — all prediction cards (plan-gate decision: all cards in v1).
 *
 * One aggregate fetch (`/analysis/predictions`) renders every card. The
 * backend's nightly fan-out populates the tables; this page is read-only.
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

  const topBrokers = [...brokers].sort((a, b) => b.win_probability - a.win_probability).slice(0, 10)
  const slowPayers = brokers.filter((b) => b.is_slow_payer).slice(0, 10)
  const churnWarnings = [...brokers].filter((b) => b.churn_risk > 0.3).sort((a, b) => b.churn_risk - a.churn_risk).slice(0, 10)
  const bestHours = brokers.filter((b) => b.best_send_hour !== null && b.best_send_hour !== undefined).slice(0, 10)

  return (
    <>
      <PageHeader
        eyebrow="Analyze"
        title="Intelligence"
        description={`Nightly predictions over the inbox. ${brokers.length} broker${brokers.length === 1 ? "" : "s"} analyzed, ${lanes.length} lane${lanes.length === 1 ? "" : "s"}.`}
      />

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
    </>
  )
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
