import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TableSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton withActions />
      <div className="mb-5 grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <PanelSkeleton>
          <Skeleton className="h-24 w-full" aria-hidden />
        </PanelSkeleton>
        <PanelSkeleton>
          <div className="space-y-2" aria-hidden>
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-4 w-full" />
            ))}
          </div>
        </PanelSkeleton>
      </div>
      <PanelSkeleton bodyClassName="p-0">
        <TableSkeleton rows={12} cols={6} />
      </PanelSkeleton>
    </>
  )
}
