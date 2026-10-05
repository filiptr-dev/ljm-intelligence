import {
  PageHeaderSkeleton,
  PanelSkeleton,
} from "@/components/app/page-skeletons"
import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton withActions />
      <div className="grid gap-5 lg:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <PanelSkeleton key={i}>
            <div className="space-y-3" aria-hidden>
              {Array.from({ length: 5 }).map((_, j) => (
                <div key={j} className="flex items-center justify-between gap-3">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-6 w-12" />
                </div>
              ))}
            </div>
          </PanelSkeleton>
        ))}
      </div>
      <div className="mt-6">
        <PanelSkeleton>
          <div className="space-y-3" aria-hidden>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </PanelSkeleton>
      </div>
    </>
  )
}
