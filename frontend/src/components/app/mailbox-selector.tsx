"use client"

import * as React from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { cn } from "@/lib/utils"

export type MailboxOption = { address: string }

/**
 * MailboxSelector — multi-select chips row.
 *
 * Every allowlisted mailbox is a chip (local part shown, full address in the
 * title). Clicking a chip toggles its membership in the current selection.
 * "All" clears the selection. The selection lives in the URL as repeated
 * ``?mailbox=…`` params so the choice survives refresh and the server
 * component can read ``searchParams.mailbox`` as a ``string[]`` and pass it
 * straight to the typed inbox client — no client store needed.
 *
 * We replaced the earlier >8 → ``<select>`` switch with wrap-friendly chips
 * because the real LJM allowlist is 31 mailboxes and the operator wants to
 * narrow by 2-3 at a time (same look as the viktor / contact pills).
 */
export function MailboxSelector({ options }: { options: MailboxOption[] }) {
  const router = useRouter()
  const pathname = usePathname()
  const sp = useSearchParams()
  // Repeated ``?mailbox=`` wins; a single ``?mailbox=a,b`` CSV is also honoured
  // (same convention the backend accepts). ``getAll`` already returns [] when
  // the param isn't present.
  const selected = React.useMemo<string[]>(() => {
    const all = sp.getAll("mailbox")
    const flat: string[] = []
    for (const v of all) {
      for (const part of v.split(",")) {
        const t = part.trim()
        if (t && !flat.includes(t)) flat.push(t)
      }
    }
    return flat
  }, [sp])

  const update = React.useCallback(
    (next: string[]) => {
      const qs = new URLSearchParams()
      // Preserve non-mailbox params; drop pagination (p1 of a bigger set is
      // not the same as p1 of a narrower one).
      for (const [k, v] of sp.entries()) {
        if (k === "mailbox" || k === "page") continue
        qs.append(k, v)
      }
      for (const m of next) qs.append("mailbox", m)
      const s = qs.toString()
      router.replace(s ? `${pathname}?${s}` : pathname)
    },
    [pathname, router, sp],
  )

  if (options.length === 0) return null

  const shortLabel = (addr: string) => addr.split("@")[0] || addr
  const toggle = (addr: string) => {
    const has = selected.includes(addr)
    update(has ? selected.filter((x) => x !== addr) : [...selected, addr])
  }

  // ``flex-wrap`` with small gaps keeps 31 entries legible without eating
  // the whole page, and the row still fits inside a 390px viewport. Each
  // chip hits the 24px tap-target floor on touch.
  return (
    <div className="mb-3 flex flex-wrap items-center gap-1 rounded-sm border border-border bg-card p-1 text-xs sm:text-sm">
      <span className="px-2 text-[0.65rem] uppercase tracking-wide text-muted-foreground sm:text-xs">Inbox:</span>
      <button
        type="button"
        onClick={() => update([])}
        className={cn(
          "rounded-sm px-2 py-0.5",
          selected.length === 0 ? "bg-destructive text-white" : "hover:bg-muted",
        )}
      >
        All
      </button>
      {options.map((o) => {
        const on = selected.includes(o.address)
        return (
          <button
            key={o.address}
            type="button"
            onClick={() => toggle(o.address)}
            title={o.address}
            aria-pressed={on}
            className={cn(
              "rounded-sm px-2 py-0.5",
              on ? "bg-destructive text-white" : "hover:bg-muted",
            )}
          >
            {shortLabel(o.address)}@
          </button>
        )
      })}
    </div>
  )
}
