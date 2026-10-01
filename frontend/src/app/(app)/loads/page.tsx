import { LoadsBoard } from "./loads-board"
import { PageHeader } from "@/components/app/ui"

export const dynamic = "force-dynamic"

export default function LoadsPage() {
  return (
    <>
      <PageHeader
        eyebrow="Live loads"
        title="Loads board"
        description="One internal view across every load source we have signed up for. Vendors you have not activated show as 'needs credentials' — enable them in Settings → Load sources."
      />
      <LoadsBoard />
    </>
  )
}
