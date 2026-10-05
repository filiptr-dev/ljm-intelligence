import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TableSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <div className="mb-4 flex flex-wrap items-center gap-2" aria-hidden>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-24" />
        ))}
        <Skeleton className="ml-auto h-9 w-56" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <PanelSkeleton bodyClassName="p-0">
          <TableSkeleton rows={14} cols={5} />
        </PanelSkeleton>
        <PanelSkeleton>
          <div className="space-y-3" aria-hidden>
            <Skeleton className="h-5 w-2/3" />
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-4 w-full" />
            ))}
          </div>
        </PanelSkeleton>
      </div>
    </>
  )
}
