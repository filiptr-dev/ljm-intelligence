/**
 * Freight-manager segment feed — /contacts/freight-managers.
 * SERVER COMPONENT shell. Interactive bits (campaign modal, pagination) live
 * in the client island.
 */

import { listFreightManagers, type Contact } from "@/lib/api/contacts"
import { PageHeader } from "@/components/app/ui"
import FreightManagersClient from "./client"

export const dynamic = "force-dynamic"

export default async function FreightManagersPage() {
  let initial: { items: Contact[]; next_cursor: string | null } = { items: [], next_cursor: null }
  try {
    const data = await listFreightManagers(undefined, 50)
    initial = {
      items: data.items ?? [],
      next_cursor: data.next_cursor ?? null,
    }
  } catch {
    // Degrade silently — the client island will show "no contacts yet".
  }
  return (
    <>
      <PageHeader
        eyebrow="Contacts"
        title="Freight managers"
        description="Every contact across your leads whose job title says Logistics / Shipping / Supply Chain. Build a one-click email campaign to the whole segment."
      />
      <FreightManagersClient initial={initial} />
    </>
  )
}
