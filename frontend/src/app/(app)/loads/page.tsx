import { LoadsBoard } from "./loads-board"
import { PageHeader } from "@/components/app/ui"
import { LaneStrip } from "@/components/analytics/LaneStrip"
import { LoadstopSyncButton } from "@/components/app/loadstop-sync-button"

export const dynamic = "force-dynamic"

export default function LoadsPage() {
  return (
    <>
      <PageHeader
        eyebrow="Live loads"
        title="Loads board"
        description="One internal view across every load source we have signed up for. Vendors you have not activated show as 'needs credentials' — enable them in Settings → Load sources."
        actions={
          <div className="flex items-center gap-2">
            <LoadstopSyncButton service="loads" label="Sync loads" />
            <LoadstopSyncButton service="all" label="Sync all LoadStop" showCaption={false} />
          </div>
        }
      />
      <LaneStrip />
      <LoadsBoard />
    </>
  )
}
