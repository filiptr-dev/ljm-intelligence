import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TilesSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <div className="mb-3" aria-hidden>
        <Skeleton className="h-4 w-32" />
      </div>
      <PageHeaderSkeleton withActions />
      <TilesSkeleton count={4} />
      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <PanelSkeleton>
            <div className="space-y-3" aria-hidden>
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          </PanelSkeleton>
          <PanelSkeleton>
            <div className="space-y-2" aria-hidden>
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          </PanelSkeleton>
        </div>
        <div className="space-y-5">
          <PanelSkeleton>
            <div className="space-y-2" aria-hidden>
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-4 w-full" />
              ))}
            </div>
          </PanelSkeleton>
          <PanelSkeleton>
            <Skeleton className="h-40 w-full" aria-hidden />
          </PanelSkeleton>
        </div>
      </div>
    </>
  )
}
