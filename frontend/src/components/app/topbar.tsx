"use client"

import Link from "next/link"
import { Tire } from "@/components/brand/tire"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { useSession } from "@/lib/auth/session"
import { num } from "@/lib/format"
import { useTodayCounters } from "./use-today-counters"

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

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:px-8">
      <SidebarTrigger className="-ml-1" />
      <Link
        href="/leads"
        className="flex items-center gap-2 rounded-sm border border-asphalt bg-asphalt py-1 pr-3 pl-1.5 text-white transition-colors hover:bg-asphalt-2"
      >
        <Tire spinning className="size-6" />
        <span className="font-display text-sm font-semibold tracking-[0.12em]">Monitoring active</span>
        <span className="size-2 animate-beacon rounded-full bg-safety" aria-hidden />
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

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="num font-mono font-semibold text-foreground">{num(value)}</dd>
    </div>
  )
}
