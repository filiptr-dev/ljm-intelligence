import { PageHeaderSkeleton } from "@/components/app/page-skeletons"
import { ChartSkeleton } from "@/components/charts/primitives"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <ChartSkeleton className="h-96" />
    </>
  )
}
