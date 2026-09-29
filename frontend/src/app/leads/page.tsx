import { LeadFinder } from "@/components/app/lead-finder"
import { PageHeader } from "@/components/app/ui"
import { EUTruck } from "@/components/brand/trucks"
import { getStore } from "@/lib/data/store"

export default async function LeadsPage() {
  const s = await getStore()
  return (
    <>
      <PageHeader
        eyebrow="Lead finder"
        title="New customers for your trucks"
        description="The crawler looks for everyone who needs transport: freight brokers, companies that ship their own freight, and forwarders / 3PLs. It checks registries, load boards and business directories around the clock and scores every company against the customers you already win with."
        art={<EUTruck spinning />}
      />
      <LeadFinder pool={s.leads} profile={s.profile} />
    </>
  )
}
