/**
 * Route-segment loading UI for the (app) group. Rendered by Next.js the
 * instant the user navigates into any (app) route and the server has not
 * yet emitted its first byte. Keeps the sidebar/topbar from the layout and
 * shows the Overview-shaped skeleton underneath — same shape as the real
 * Overview page to avoid layout shift when the server streams in.
 */

import * as React from "react"
import { PageHeader } from "@/components/app/ui"
import { OverviewSkeleton } from "./overview-skeleton"

export default function AppLoading() {
  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="Today"
        description="Who to call and what to chase today."
      />
      <OverviewSkeleton />
    </>
  )
}
