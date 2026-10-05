import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TableSkeleton,
} from "@/components/app/page-skeletons"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton />
      <div className="grid gap-5 lg:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <PanelSkeleton key={i} bodyClassName="p-0">
            <TableSkeleton rows={8} cols={4} />
          </PanelSkeleton>
        ))}
      </div>
    </>
  )
}
