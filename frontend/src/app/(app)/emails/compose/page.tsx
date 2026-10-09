import { PageHeader } from "@/components/app/ui"
import type { ContactOption } from "@/components/app/email-composer"
import { getBroker } from "@/lib/api/brokers"
import { ComposeClient } from "./compose-client"

/**
 * Full-page new-email composer — restored rich builder from pre-`0333732`,
 * wired to the real `/inbox/compose` endpoint via `singleSendAdapter`.
 *
 * Reached from the broker profile "Draft email", the "New email" button on
 * /messages, lead-finder bulk actions, etc. The owner switch decides
 * whether the message actually leaves the building (default OFF →
 * simulated-sender records a receipt).
 */
export default async function ComposePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams
  const to = typeof sp.to === "string" ? sp.to : ""
  const subject = typeof sp.subject === "string" ? sp.subject : ""
  const body = typeof sp.body === "string" ? sp.body : ""
  const brokerId = typeof sp.broker === "string" ? sp.broker : ""

  // Resolve broker/lead by id into a prefilled recipient when `?broker=<id>`
  // is passed (Follow-ups "Draft email" deep-link). Reuses the typed
  // `getBroker` client so we don't invent a new lookup; falls back silently
  // if the lookup fails so the composer still renders empty.
  let brokerRecipient: ContactOption | undefined
  if (brokerId) {
    try {
      const detail = await getBroker(brokerId)
      const b = detail.broker
      const email = b.primary_email?.value ?? ""
      // Contact names come back as ContactFieldOut; unwrap to a plain string.
      const contactName = b.contacts?.[0]?.name?.value ?? b.name
      // BUG 4 — populate lane from the broker's main lane so the builder's
      // `{{lane}}` tag renders real cities (e.g. "Dallas → Chicago") instead
      // of the "your lanes" fallback whenever we have real data.
      const laneLabel =
        b.main_lane && b.main_lane.origin && b.main_lane.destination
          ? `${b.main_lane.origin} → ${b.main_lane.destination}`
          : undefined
      brokerRecipient = {
        id: b.id,
        name: b.name,
        contactName,
        email,
        region: "US",
        kind: "broker",
        lane: laneLabel,
        equipment: undefined,
        sub: email || b.state || "Broker",
      }
    } catch {
      brokerRecipient = undefined
    }
  }

  // When only an email was passed (today's query-param path), synthesise a
  // minimal `Recipient` so the picker starts with the recipient pre-filled.
  // When a lead/broker id is passed, the picker will find them via the
  // existing `useBackendLeads`/`useEngine` lookups.
  const initialRecipient: ContactOption | undefined = brokerRecipient
    ? brokerRecipient
    : to
    ? {
        id: `addr:${to}`,
        name: to.split("@", 2)[1] ?? to,
        contactName: to.split("@", 1)[0] ?? to,
        email: to,
        region: "US",
        kind: "broker",
        lane: undefined,
        equipment: undefined,
        sub: "Direct recipient",
      }
    : undefined

  return (
    <>
      <PageHeader
        eyebrow="Grow"
        title="New email"
        description="A personal email to one company, outside of a campaign. The owner switch decides whether it leaves the building."
      />
      <ComposeClient
        initialRecipient={initialRecipient}
        initialSubject={subject}
        initialBody={body}
      />
    </>
  )
}
