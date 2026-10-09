import { PageHeaderSkeleton, PanelSkeleton } from "@/components/app/page-skeletons"
import { ChartSkeleton } from "@/components/charts/primitives"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <div className="mb-5 grid gap-3 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => <ChartSkeleton key={i} className="h-24" />)}
      </div>
      <PanelSkeleton><ChartSkeleton className="h-96" /></PanelSkeleton>
    </>
  )
}
