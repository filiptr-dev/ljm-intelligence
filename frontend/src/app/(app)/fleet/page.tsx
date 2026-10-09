import { PageHeader } from "@/components/app/ui"
import { FleetView } from "@/components/app/fleet/fleet-view"
import { EmptyChart } from "@/components/charts/primitives"
import { ApiRequestError } from "@/lib/api/client"
import { getAlerts, getTrucks } from "@/lib/api/fleet"
import { OverviewRetryButton } from "../overview-retry"

// Server-fetched with the session cookie: never prerender, and give a Render/Neon cold start room to finish.
export const dynamic = "force-dynamic"
export const maxDuration = 60

const ATTEMPTS = 3
const BACKOFF_MS = 2_000

/**
 * The API sleeps on the free tier, and the first requests after a wake-up answer 5xx or drop. The
 * client already retries once; here we keep trying for a few seconds instead of painting the error
 * on the first miss. A 4xx (auth, validation) is a real answer, so it is never retried.
 */
async function withColdStartRetry<T>(run: () => Promise<T>): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await run()
    } catch (e) {
      const final = attempt >= ATTEMPTS || (e instanceof ApiRequestError && e.status >= 400 && e.status < 500)
      if (final) throw e
      await new Promise((r) => setTimeout(r, BACKOFF_MS * attempt))
    }
  }
}

/**
 * Fleet (Kamioni) — server-rendered shell. The list and the alerts strip load in
 * parallel; the unit drawer fetches its own detail in the browser when opened.
 */
export default async function FleetPage() {
  const data = await withColdStartRetry(() => Promise.all([getTrucks(), getAlerts()])).then(
    ([list, alerts]) => ({ list, alerts }),
    (e: unknown) => (e instanceof Error ? e : new Error("unknown error")),
  )
  if (data instanceof Error) {
    return (
      <>
        <PageHeader eyebrow="Tools" title="Fleet" />
        <EmptyChart
          title="Could not load the fleet"
          hint={`The API did not answer (${data.message}). Try again in a moment.`}
          action={<OverviewRetryButton />}
        />
      </>
    )
  }
  return <FleetView initial={data} />
}
