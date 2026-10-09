import { PageHeader } from "@/components/app/ui"
import { FleetView } from "@/components/app/fleet/fleet-view"
import { EmptyChart } from "@/components/charts/primitives"
import { getAlerts, getTrucks } from "@/lib/api/fleet"

/**
 * Fleet (Kamioni) — server-rendered shell. The list and the alerts strip load in
 * parallel; the unit drawer fetches its own detail in the browser when opened.
 */
export default async function FleetPage() {
  const data = await Promise.all([getTrucks(), getAlerts()]).then(
    ([list, alerts]) => ({ list, alerts }),
    (e: unknown) => (e instanceof Error ? e : new Error("unknown error")),
  )
  if (data instanceof Error) {
    return (
      <>
        <PageHeader eyebrow="Tools" title="Fleet" />
        <EmptyChart title="Could not load the fleet" hint={`The API did not answer (${data.message}). Reload in a moment.`} />
      </>
    )
  }
  return <FleetView initial={data} />
}
