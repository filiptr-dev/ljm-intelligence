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
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-24" />
        ))}
        <Skeleton className="ml-auto h-9 w-64" />
      </div>
      <PanelSkeleton bodyClassName="p-0">
        <TableSkeleton rows={14} cols={7} />
      </PanelSkeleton>
    </>
  )
}
