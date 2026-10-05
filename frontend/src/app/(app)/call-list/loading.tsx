import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TableSkeleton,
  TilesSkeleton,
} from "@/components/app/page-skeletons"

export default function Loading() {
  return (
    <>
      <TilesSkeleton count={4} className="mb-5" />
      <PageHeaderSkeleton withActions />
      <PanelSkeleton bodyClassName="p-0">
        <TableSkeleton rows={12} cols={6} />
      </PanelSkeleton>
    </>
  )
}
