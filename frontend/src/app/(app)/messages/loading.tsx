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
      <div className="mb-4 flex items-center gap-2" aria-hidden>
        <Skeleton className="h-9 w-28" />
        <Skeleton className="h-9 w-24" />
      </div>
      <PanelSkeleton bodyClassName="p-0">
        <TableSkeleton rows={8} cols={5} />
      </PanelSkeleton>
      <div className="mt-5">
        <PanelSkeleton bodyClassName="p-0">
          <TableSkeleton rows={8} cols={5} />
        </PanelSkeleton>
      </div>
      <div className="mt-5">
        <PanelSkeleton bodyClassName="p-0">
          <TableSkeleton rows={6} cols={4} />
        </PanelSkeleton>
      </div>
    </>
  )
}
