"use client"

import * as React from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { cn } from "@/lib/utils"

export type MailboxOption = { address: string }

/**
 * MailboxSelector — segmented "All · contact@ · viktor@ …" control.
 *
 * Parks the chosen mailbox in `?mailbox=` so the choice survives refresh
 * and persists when navigating between `/emails` and `/messages` without
 * a client store. "All" drops the param. The server pages read
 * `searchParams.mailbox` and pass it to the typed inbox client.
 */
export function MailboxSelector({ options }: { options: MailboxOption[] }) {
  const router = useRouter()
  const pathname = usePathname()
  const sp = useSearchParams()
  const current = sp.get("mailbox") || ""

  const update = React.useCallback(
    (addr: string | null) => {
      const next = new URLSearchParams(Array.from(sp.entries()))
      // Reset pagination when the scope changes — p1 of a bigger set is not
      // the same as p1 of a narrower one.
      next.delete("page")
      if (addr) next.set("mailbox", addr)
      else next.delete("mailbox")
      const qs = next.toString()
      router.replace(qs ? `${pathname}?${qs}` : pathname)
    },
    [pathname, router, sp],
  )

  if (options.length === 0) return null

  const shortLabel = (addr: string) => addr.split("@")[0] || addr

  return (
    <div className="mb-3 flex flex-wrap items-center gap-1 rounded-sm border border-border bg-card p-1 text-sm">
      <span className="px-2 text-xs uppercase tracking-wide text-muted-foreground">Inbox:</span>
      <button
        type="button"
        onClick={() => update(null)}
        className={cn(
          "rounded-sm px-2.5 py-1",
          !current ? "bg-asphalt text-white" : "hover:bg-muted",
        )}
      >
        All
      </button>
      {options.map((o) => (
        <button
          key={o.address}
          type="button"
          onClick={() => update(o.address)}
          title={o.address}
          className={cn(
            "rounded-sm px-2.5 py-1",
            current === o.address ? "bg-asphalt text-white" : "hover:bg-muted",
          )}
        >
          {shortLabel(o.address)}@
        </button>
      ))}
    </div>
  )
}
