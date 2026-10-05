import {
  PageHeaderSkeleton,
  PanelSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <PanelSkeleton>
            <div className="space-y-3" aria-hidden>
              <Skeleton className="h-9 w-full" />
              <div className="flex gap-2">
                <Skeleton className="h-9 w-24" />
                <Skeleton className="h-9 w-24" />
                <Skeleton className="h-9 w-24" />
              </div>
            </div>
          </PanelSkeleton>
          <PanelSkeleton>
            <div className="space-y-3" aria-hidden>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          </PanelSkeleton>
        </div>
        <PanelSkeleton>
          <div className="space-y-2" aria-hidden>
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </PanelSkeleton>
      </div>
    </>
  )
}
