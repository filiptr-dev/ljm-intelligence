import { OutreachBuilder, type ExistingRow } from "@/components/app/outreach-builder"
import { PageHeader } from "@/components/app/ui"
import { getStore } from "@/lib/data/store"

export default async function OutreachPage({ searchParams }: PageProps<"/outreach">) {
  const sp = await searchParams
  const s = await getStore()
  const str = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : undefined)

  const existing: ExistingRow[] = s.brokers.map((b) => {
    const st = s.statsById.get(b.id)!
    const lane = st.topLane ?? `${b.lanes[0].origin} → ${b.lanes[0].destination}`
    return {
      id: b.id,
      name: b.name,
      region: b.region,
      segment: st.segment,
      contactName: b.contact.name,
      email: b.contact.email,
      lane,
      equipment: b.equipment[0],
      health: st.health,
      daysSinceLast: st.daysSinceLast,
      booked: st.booked,
    }
  })

  return (
    <>
      <PageHeader
        eyebrow="Campaigns"
        title="New campaign"
        description="Set a goal, pick who gets it (new leads, existing brokers or both), write the message with AI, add automatic follow-ups, then launch now or schedule it."
      />
      <OutreachBuilder
        leads={s.leads}
        existing={existing}
        initial={{
          audience: (str("audience") as "new" | "existing" | "both") ?? "new",
          ids: str("ids")?.split(",").filter(Boolean) ?? [],
          campaign: str("campaign") ?? undefined,
          templateId: str("template"),
          // past campaigns are on the server; ones sent from this browser are looked up client-side
          template: s.campaignHistory.find((c) => c.id === str("template")) ?? null,
          segment: str("segment") ?? undefined,
        }}
      />
    </>
  )
}
