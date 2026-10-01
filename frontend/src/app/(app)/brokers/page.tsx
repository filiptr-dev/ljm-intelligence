import { BrokersTable, type BrokerRow } from "@/components/app/brokers-table"
import { PageHeader } from "@/components/app/ui"
import { getStore } from "@/lib/data/store"

export default async function BrokersPage({ searchParams }: PageProps<"/brokers">) {
  const sp = await searchParams
  const s = await getStore()
  const rows: BrokerRow[] = s.brokers.map((b) => {
    const st = s.statsById.get(b.id)!
    return {
      id: b.id,
      name: b.name,
      region: b.region,
      country: b.country,
      hq: b.hq,
      registration: b.registration,
      contact: b.contact.name,
      segment: st.segment,
      booked: st.booked,
      rejected: st.rejected,
      winRate: st.winRate,
      revenue: st.revenue,
      health: st.health,
      healthDelta: st.healthDelta,
      daysSinceLast: st.daysSinceLast,
      monthly: st.monthly,
      paymentIssues: st.paymentIssues,
    }
  })

  return (
    <>
      <PageHeader
        eyebrow="Brokers"
        title="Broker database"
        description="Every broker you have emailed, profiled by the AI. Health combines recency, win rate, tone and volume."
      />
      <BrokersTable
        rows={rows}
        initialSegment={typeof sp.segment === "string" ? sp.segment : "All"}
        initialSort={typeof sp.sort === "string" ? sp.sort : "health"}
      />
    </>
  )
}
