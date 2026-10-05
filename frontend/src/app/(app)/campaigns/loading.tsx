import {
  PageHeaderSkeleton,
  PanelSkeleton,
  TableSkeleton,
  TilesSkeleton,
} from "@/components/app/page-skeletons"

export default function Loading() {
  return (
    <>
      <PageHeaderSkeleton withActions />
      <TilesSkeleton count={4} />
      <div className="mt-5">
        <PanelSkeleton bodyClassName="p-0">
          <TableSkeleton rows={10} cols={6} />
        </PanelSkeleton>
      </div>
    </>
  )
}
