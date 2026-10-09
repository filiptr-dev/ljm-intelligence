import { PageHeaderSkeleton, PanelSkeleton } from "@/components/app/page-skeletons"
import { ChartSkeleton } from "@/components/charts/primitives"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => <ChartSkeleton key={i} className="h-28" />)}
      </div>
      <div className="mt-5 grid gap-5 xl:grid-cols-3">
        <PanelSkeleton><ChartSkeleton className="h-72" /></PanelSkeleton>
        <PanelSkeleton><ChartSkeleton className="h-56" /></PanelSkeleton>
      </div>
    </>
  )
}
