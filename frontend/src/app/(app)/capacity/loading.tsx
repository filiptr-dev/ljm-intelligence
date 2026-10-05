import {
  PageHeaderSkeleton,
  PanelSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <PanelSkeleton>
          <div className="space-y-3" aria-hidden>
            <div className="flex gap-2">
              <Skeleton className="h-9 w-28" />
              <Skeleton className="h-9 w-28" />
            </div>
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-2/3" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-9 w-32" />
          </div>
        </PanelSkeleton>
        <PanelSkeleton>
          <div className="space-y-2" aria-hidden>
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        </PanelSkeleton>
      </div>
    </>
  )
}
