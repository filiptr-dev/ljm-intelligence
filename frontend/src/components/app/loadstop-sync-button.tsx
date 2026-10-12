"use client"

/**
 * LoadStop Sync button — one-click operator refresh for a given service.
 *
 * Reads the slim ``GET /loadboards/loadstop`` to resolve the disabled state
 * (``configured: false`` → disabled) and the "last synced" caption from the
 * per-service timestamp. The POST goes through the typed client (per the
 * frontend-backend-pattern-consistency memory — never raw ``/api/...``).
 */

import * as React from "react"
import { toast } from "sonner"
import { RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { getLoadstopState, syncLoadstop, type LoadstopState, type LoadstopSyncService } from "@/lib/api/loadstop"

type Props = {
  service: LoadstopSyncService
  label?: string
  /** When true the caption shows ago-style text next to the button. */
  showCaption?: boolean
  className?: string
}

function since(iso: string | null | undefined): string | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return null
  const diff = Date.now() - t
  const mins = Math.round(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

export function LoadstopSyncButton({ service, label, showCaption = true, className }: Props) {
  const [state, setState] = React.useState<LoadstopState | null>(null)
  const [busy, setBusy] = React.useState(false)

  const refresh = React.useCallback(async () => {
    try {
      const s = await getLoadstopState()
      setState(s)
    } catch {
      // silent — the button stays disabled on failure
    }
  }, [])

  React.useEffect(() => {
    void refresh()
  }, [refresh])

  const key: keyof LoadstopState["last_sync"] | null =
    service === "all" ? null : (service as keyof LoadstopState["last_sync"])
  const last = state && key ? state.last_sync[key] : null
  const ago = since(last)

  const disabled = !state || !state.configured || busy
  const title = !state
    ? "Loading…"
    : !state.configured
      ? "LoadStop env creds not set"
      : state.kill_switch_on
        ? "LOADSTOP_INGEST_KILL is on"
        : undefined

  const onClick = async () => {
    setBusy(true)
    try {
      const r = await syncLoadstop(service)
      if (r.ok) {
        toast.success(`${label ?? `Sync ${service}`} queued`)
        setTimeout(() => void refresh(), 1_500)
      } else {
        toast.error(`Sync refused`, { description: r.reason ?? `HTTP ${r.status}` })
      }
    } catch (e) {
      toast.error("Sync failed", { description: String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className={className}>
      <Button type="button" size="sm" variant="outline" onClick={onClick} disabled={disabled} title={title}>
        <RefreshCw className={busy ? "animate-spin" : undefined} /> {label ?? `Sync ${service}`}
      </Button>
      {showCaption && ago ? (
        <span className="ml-2 text-xs text-muted-foreground">last sync {ago}</span>
      ) : null}
    </span>
  )
}
