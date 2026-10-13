"use client"

import Link from "next/link"
import { useCallback, useEffect, useRef, useState } from "react"
import { BellIcon } from "lucide-react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "@/lib/api/notifications"
import { timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

/**
 * Bell + unread badge in the topbar.
 *
 * Why polling (45s): v1 simplicity, no websocket plumbing. 45s is well
 * under the 15-min `mail_incremental` tick, so a new broker reply
 * surfaces within one tick + one poll (~15–16 min worst case).
 */
export function NotificationsBell() {
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unread, setUnread] = useState(0)
  const [open, setOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    try {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      const data = await listNotifications(50, ctrl.signal)
      setItems(data.items ?? [])
      setUnread(data.unread ?? 0)
    } catch {
      // Silent — the bell never crashes the shell.
    }
  }, [])

  useEffect(() => {
    // Fire-and-forget the first poll from a microtask so React's rules
    // against setState-within-effect-body don't flag it; the setInterval
    // then schedules the recurring refresh.
    const t = setTimeout(() => void refresh(), 0)
    const id = setInterval(() => void refresh(), 45_000)
    return () => {
      clearTimeout(t)
      clearInterval(id)
      abortRef.current?.abort()
    }
  }, [refresh])

  async function onClickRow(n: NotificationItem) {
    if (n.read_at == null) {
      try {
        await markNotificationRead(n.id)
      } catch {
        /* ignore */
      }
    }
    void refresh()
  }

  async function onMarkAll() {
    try {
      await markAllNotificationsRead()
    } catch {
      /* ignore */
    }
    void refresh()
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <button
            type="button"
            aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
            className="relative rounded border border-border p-1.5 hover:bg-muted"
          />
        }
      >
        <BellIcon className="size-4" />
        {unread > 0 ? (
          <span
            aria-hidden
            className="absolute -top-1 -right-1 rounded-full bg-destructive px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white"
          >
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </SheetTrigger>
      <SheetContent side="right" className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Notifications</SheetTitle>
          <div className="flex items-center justify-between gap-2 pt-1">
            <span className="text-xs text-muted-foreground">
              {unread > 0 ? `${unread} unread` : "All caught up"}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onMarkAll}
              disabled={unread === 0}
            >
              Mark all read
            </Button>
          </div>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto border-t border-border">
          {items.length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">
              No notifications yet.
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((n) => {
                const href = targetHref(n)
                return (
                  <li
                    key={n.id}
                    className={cn(
                      "p-3",
                      n.read_at == null ? "bg-muted/40" : undefined,
                    )}
                  >
                    <Link
                      href={href}
                      className="block"
                      onClick={() => void onClickRow(n)}
                    >
                      <div className="flex items-center gap-2">
                        <KindDot kind={n.kind} />
                        <span className="font-semibold text-sm">{n.title}</span>
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {timeAgo(n.created_at as unknown as string)}
                      </div>
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function targetHref(n: NotificationItem): string {
  if (n.kind === "load_offer" && n.load_id != null) return `/loads/${n.load_id}`
  if (n.thread_id) return `/emails/${encodeURIComponent(n.thread_id)}`
  return "/emails"
}

function KindDot({ kind }: { kind: string }) {
  const className =
    kind === "urgent_email"
      ? "bg-destructive"
      : kind === "broker_reply"
        ? "bg-safety"
        : "bg-primary"
  return <span aria-hidden className={cn("size-2 rounded-full", className)} />
}
