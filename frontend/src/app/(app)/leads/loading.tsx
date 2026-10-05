import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TableSkeleton,
  TilesSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton withArt />
      <TilesSkeleton count={4} />
      <div className="mt-5 mb-4 flex flex-wrap items-center gap-2" aria-hidden>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-28" />
        ))}
        <Skeleton className="ml-auto h-9 w-56" />
      </div>
      <PanelSkeleton bodyClassName="p-0">
        <TableSkeleton rows={12} cols={6} />
      </PanelSkeleton>
    </>
  )
}
