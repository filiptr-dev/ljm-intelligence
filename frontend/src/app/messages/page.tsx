import type { ComposerInit, ContactOption } from "@/components/app/email-composer"
import { EmailsInbox } from "@/components/app/emails-inbox"
import type { EmailPurpose } from "@/lib/ai/types"
import { analyzeCampaigns } from "@/lib/campaigns/metrics"
import { getStore } from "@/lib/data/store"
import { LEAD_KIND_LABEL } from "@/lib/data/types"

export default async function EmailsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams
  const s = await getStore()
  const str = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : undefined)

  const contacts: ContactOption[] = [
    ...s.brokers.map((b) => {
      const st = s.statsById.get(b.id)!
      return {
        id: b.id, kind: "broker" as const, name: b.name, contactName: b.contact.name, email: b.contact.email, region: b.region,
        lane: st.topLane ?? `${b.lanes[0].origin} → ${b.lanes[0].destination}`, equipment: b.equipment[0], verified: true,
        segment: st.segment, sub: `${b.hq} · ${st.booked} loads booked`,
      }
    }),
    ...s.leads.map((l) => ({
      id: l.id, kind: "lead" as const, companyType: l.kind, name: l.name, contactName: l.contact.name, email: l.contact.email, region: l.region,
      lane: `${l.lanes[0].origin} → ${l.lanes[0].destination}`, equipment: l.equipment[0], verified: l.emailVerified,
      sub: `${LEAD_KIND_LABEL[l.kind]} · new lead · ${l.hq}`,
    })),
  ]
  const t = analyzeCampaigns(s.campaignHistory).totals
  const initial: ComposerInit = { to: str("to"), purpose: str("purpose") as EmailPurpose | undefined }

  // how much more often a quote wins when it goes out in the fastest reply bucket vs the slowest
  const [fast, , , slow] = s.responseBuckets
  const speedLift = slow?.winRate ? fast.winRate / slow.winRate : 1

  return <EmailsInbox history={s.emailHistory} contacts={contacts} campaignReplyRate={t.replied / (t.delivered || 1)} speedLift={speedLift} initial={initial} />
}
