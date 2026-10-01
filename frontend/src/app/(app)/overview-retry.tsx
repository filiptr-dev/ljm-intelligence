"use client"

/**
 * Overview retry island — the only interactive sliver of the Today page.
 *
 * The page itself is a Server Component now; the data read happens
 * server-side using the HttpOnly session cookie, no bearer in JS. If the
 * backend was unreachable we show an error panel with this small button,
 * which just calls `router.refresh()` to re-run the server render.
 */

import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"

export function OverviewRetryButton() {
  const router = useRouter()
  return (
    <Button variant="outline" size="sm" onClick={() => router.refresh()}>
      Retry
    </Button>
  )
}
