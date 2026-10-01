import type { ContactOption } from "@/components/app/email-composer"
import { SingleEmailBuilder } from "@/components/app/single-email-builder"
import { PageHeader } from "@/components/app/ui"
import type { EmailPurpose } from "@/lib/ai/types"
import { getStore } from "@/lib/data/store"
import { LEAD_KIND_LABEL } from "@/lib/data/types"

/**
 * Full-page single-recipient email composer. No sheet, no dialog.
 * Reached from the broker profile "Draft email" and from the "New email"
 * button on /messages.
 */
export default async function ComposePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams
  const str = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : undefined)

  const s = await getStore()
  const brokerId = str("broker")
  const leadId = str("lead")
  const purpose = str("purpose") as EmailPurpose | undefined

  // Same shape /emails builds — broker + lead contacts as ContactOption[]
  const contacts: ContactOption[] = [
    ...s.brokers.map((b) => {
      const st = s.statsById.get(b.id)!
      return {
        id: b.id,
        kind: "broker" as const,
        name: b.name,
        contactName: b.contact.name,
        email: b.contact.email,
        region: b.region,
        lane: st.topLane ?? `${b.lanes[0].origin} → ${b.lanes[0].destination}`,
        equipment: b.equipment[0],
        verified: true,
        segment: st.segment,
        sub: `${b.hq} · ${st.booked} loads booked`,
      }
    }),
    ...s.leads.map((l) => ({
      id: l.id,
      kind: "lead" as const,
      companyType: l.kind,
      name: l.name,
      contactName: l.contact.name,
      email: l.contact.email,
      region: l.region,
      lane: `${l.lanes[0].origin} → ${l.lanes[0].destination}`,
      equipment: l.equipment[0],
      verified: l.emailVerified,
      sub: `${LEAD_KIND_LABEL[l.kind]} · new lead · ${l.hq}`,
    })),
  ]

  // Accept either ?broker=<id> or ?lead=<id>. If the id isn't in the store
  // (e.g. a live crawled lead), the client-side SingleEmailBuilder resolves
  // it against `liveLeads` — and falls back to the picker if it can't.
  const initialToId = brokerId ?? leadId
  const to = initialToId ? contacts.find((c) => c.id === initialToId) : undefined
  const backHref = to && to.kind === "broker" ? `/brokers/${to.id}` : "/messages"
  const backLabel = to ? `Back to ${to.name}` : "Back to Emails"

  return (
    <>
      <PageHeader
        eyebrow="Grow"
        title={to ? `Draft email to ${to.name}` : "New email"}
        description={
          to
            ? `A personal email to ${to.contactName}. The AI writes the first draft from what we know about ${to.name}; you check it and send.`
            : "A personal email to one company, outside of a campaign. The AI writes it, you check it and send."
        }
      />
      <SingleEmailBuilder
        contacts={contacts}
        initialToId={initialToId}
        initialPurpose={purpose}
        backHref={backHref}
        backLabel={backLabel}
      />
    </>
  )
}
