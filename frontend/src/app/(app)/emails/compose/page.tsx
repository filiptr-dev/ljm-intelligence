import { PageHeader } from "@/components/app/ui"
import { ComposeForm } from "./compose-form"

/**
 * Full-page new-email composer backed by the real `/inbox/compose` endpoint.
 * Reached from the broker profile "Draft email" and from the "New email"
 * button on /messages.
 */
export default async function ComposePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams
  const to = typeof sp.to === "string" ? sp.to : ""

  return (
    <>
      <PageHeader
        eyebrow="Grow"
        title="New email"
        description="A personal email to one company, outside of a campaign. The owner switch decides whether it leaves the building."
      />
      <ComposeForm initialTo={to} />
    </>
  )
}
