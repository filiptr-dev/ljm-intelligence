import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft } from "lucide-react"
import { PageHeader } from "@/components/app/ui"
import { StatusChip } from "@/components/app/fleet/badges"
import { EQUIPMENT_LABEL } from "@/components/app/fleet/format"
import { TruckDetailView } from "@/components/app/fleet/truck-detail"
import { EmptyChart } from "@/components/charts/primitives"
import { ApiRequestError } from "@/lib/api/client"
import { getTruckDetail } from "@/lib/api/fleet"

/** Deep-linkable unit page — the same detail the drawer shows, with room to breathe. */
export default async function FleetUnitPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const unitId = Number(id)
  if (!Number.isInteger(unitId) || unitId < 1) notFound()
  const detail = await getTruckDetail(unitId).catch((e: unknown) => {
    if (e instanceof ApiRequestError && e.status === 404) notFound()
    return e instanceof Error ? e : new Error("unknown error")
  })
  if (detail instanceof Error) {
    return (
      <>
        <PageHeader eyebrow="Fleet" title="Unit" />
        <EmptyChart title="Could not load this unit" hint={`The API did not answer (${detail.message}). Reload in a moment.`} />
      </>
    )
  }
  const t = detail.truck
  return (
    <>
      <Link href="/fleet" className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All units
      </Link>
      <PageHeader
        eyebrow={t.kind === "trailer" ? "Fleet · Trailer" : "Fleet · Truck"}
        title={t.unit_number}
        description={`${[t.year, t.make, t.model].filter(Boolean).join(" ")}${t.equipment ? ` · ${EQUIPMENT_LABEL[t.equipment] ?? t.equipment}` : ""}`}
        actions={<StatusChip status={t.status} className="h-7 px-2.5 text-xs" />}
      />
      <TruckDetailView detail={detail} wide />
    </>
  )
}
