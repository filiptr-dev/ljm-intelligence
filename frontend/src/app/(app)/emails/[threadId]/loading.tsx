import {
  PageHeaderSkeleton,
  PanelSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <div className="mb-3" aria-hidden>
        <Skeleton className="h-4 w-28" />
      </div>
      <PageHeaderSkeleton withActions />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <PanelSkeleton key={i}>
              <div className="space-y-2" aria-hidden>
                <Skeleton className="h-3 w-40" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-11/12" />
                <Skeleton className="h-4 w-5/6" />
              </div>
            </PanelSkeleton>
          ))}
        </div>
        <div className="space-y-4">
          <PanelSkeleton>
            <div className="space-y-2" aria-hidden>
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-9 w-28" />
            </div>
          </PanelSkeleton>
          <PanelSkeleton>
            <div className="space-y-2" aria-hidden>
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-3 w-full" />
              ))}
            </div>
          </PanelSkeleton>
        </div>
      </div>
    </>
  )
}
