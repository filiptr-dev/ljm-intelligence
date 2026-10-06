"use client"

import Link from "next/link"
import { Tire } from "@/components/brand/tire"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { useSession } from "@/lib/auth/session"
import { num, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useTodayCounters, type MonitoringStatus } from "./use-today-counters"

/**
 * Top-bar pill — derived from the real crawler state.
 *
 * Before 2026-10-06 the pill always read "Monitoring active" regardless of
 * whether a crawl had ever run; now it reflects `last_crawl_status` from
 * `GET /analysis/topbar-counters` (five states, see plan).
 */
export function Topbar() {
  const counters = useTodayCounters()
  const { user, logout, status } = useSession()

  async function onLogout() {
    await logout()
    // Hard navigation (not `router.replace`) to force the whole React tree to
    // tear down. If we rely on a client-side nav, SessionProvider is reused
    // and the login page can mount with a stale `status === "authenticated"`
    // context value — its "already signed in, redirect to ?next=" effect
    // fires and bounces the user straight back to the page they tried to
    // leave. A full page load guarantees SessionProvider boots fresh and
    // `/api/auth/me` is replayed against the now-empty cookie jar.
    if (typeof window !== "undefined") window.location.replace("/login")
  }

  const pill = pillState(counters.lastCrawlStatus, counters.lastCrawlAt)

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:px-8">
      <SidebarTrigger className="-ml-1" />
      <Link
        href="/leads"
        className="flex items-center gap-2 rounded-sm border border-asphalt bg-asphalt py-1 pr-3 pl-1.5 text-white transition-colors hover:bg-asphalt-2"
        aria-label={pill.aria}
      >
        <Tire spinning={counters.lastCrawlStatus === "running"} className="size-6" />
        <span className="font-display text-sm font-semibold tracking-[0.12em]">{pill.label}</span>
        <span className={cn("size-2 rounded-full", pill.dotClass)} aria-hidden />
      </Link>
      <dl className="ml-auto hidden items-center gap-6 text-sm lg:flex">
        <Counter label="Companies scanned today" value={counters.scanned} />
        <Counter label="New leads today" value={counters.found} />
        <Counter label="Emails sent today" value={counters.sent} />
        <Counter label="Replies today" value={counters.replies} />
      </dl>
      {status === "authenticated" && user && (
        <div className="ml-4 flex items-center gap-3 text-sm">
          <span className="hidden text-muted-foreground md:inline">{user.email}</span>
          <button
            type="button"
            onClick={onLogout}
            className="rounded border border-border px-2 py-1 text-xs font-medium hover:bg-muted"
          >
            Log out
          </button>
        </div>
      )}
    </header>
  )
}

function pillState(status: MonitoringStatus, at: string | null): { label: string; dotClass: string; aria: string } {
  const rel = at ? timeAgo(at) : "never"
  switch (status) {
    case "running":
      return {
        label: "Scanning now",
        dotClass: "animate-beacon bg-safety",
        aria: "Crawler is running right now",
      }
    case "idle_recent":
      return {
        label: `Monitoring active · last scan ${rel}`,
        dotClass: "bg-safety",
        aria: `Monitoring active; last scan ${rel}`,
      }
    case "idle_stale":
      return {
        label: at ? `Idle · last scan ${rel}` : "Idle",
        dotClass: "bg-muted-foreground/60",
        aria: at ? `Idle; last scan ${rel}` : "Idle; no recent scan",
      }
    case "error":
      return {
        label: `Scan failed · ${rel}`,
        dotClass: "bg-destructive",
        aria: `Last scan failed ${rel}`,
      }
    case "none":
    default:
      return {
        label: "No scans yet",
        dotClass: "bg-muted-foreground/60",
        aria: "No scans yet",
      }
  }
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="num font-mono font-semibold text-foreground">{num(value)}</dd>
    </div>
  )
}
