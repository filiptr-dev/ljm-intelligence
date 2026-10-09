"use client"

/**
 * Integrations at a glance — one status row per integration, credentials and
 * test buttons live behind each row's Edit dialog (the existing panels, mounted
 * only while the dialog is open so they fetch fresh and keep their own state).
 *
 * Each status source is fetched independently: one failing endpoint turns only
 * its own rows into "Unknown", never the whole list.
 */

import * as React from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Panel } from "@/components/app/ui"
import { getFeatures, getUsage, type FeaturesResponse, type UsageResponse } from "@/lib/api/ai"
import {
  getLoadboardDrivers,
  getMailStatus,
  listLoadboardCreds,
  type LoadboardCredsList,
  type LoadboardDriverOut,
  type MailStatus,
} from "@/lib/api/connectors"
import { AiProvidersPanel } from "./ai-providers-panel"
import { ConnectorsPanel } from "./connectors-panel"
import { AgentBrowserPanel } from "./agent-browser-panel"
import { LoadboardsPanel, SRCS, type SrcKey } from "./loadboards-panel"
import { OwnerSwitches } from "./owner-switches"

type State = "connected" | "not_set_up" | "simulated" | "unknown"

const STATE_LABEL: Record<State, string> = {
  connected: "Connected",
  not_set_up: "Not set up",
  simulated: "Simulated",
  unknown: "Unknown",
}

function StateBadge({ state }: { state: State }) {
  const cls =
    state === "connected"
      ? "bg-green-500/10 text-green-700"
      : state === "simulated"
        ? "bg-amber-500/10 text-amber-700"
        : "bg-muted text-muted-foreground"
  return (
    <Badge variant="outline" className={cls}>
      {STATE_LABEL[state]}
    </Badge>
  )
}

type Result<T> = { data: T | null; failed: boolean }

async function settle<T>(p: Promise<T | null>): Promise<Result<T>> {
  try {
    const data = await p
    return { data, failed: data === null }
  } catch {
    return { data: null, failed: true }
  }
}

type Row = {
  id: string
  name: string
  state: State
  detail: string
  lastTested: string
}

function fmtWhen(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—"
}

export function IntegrationsStatus() {
  const [version, setVersion] = React.useState(0)
  const [open, setOpen] = React.useState<string | null>(null)
  const [features, setFeatures] = React.useState<Result<FeaturesResponse> | null>(null)
  const [usage, setUsage] = React.useState<UsageResponse | null>(null)
  const [mail, setMail] = React.useState<Result<MailStatus> | null>(null)
  const [creds, setCreds] = React.useState<Result<LoadboardCredsList> | null>(null)
  const [drivers, setDrivers] = React.useState<LoadboardDriverOut | null>(null)

  React.useEffect(() => {
    let cancelled = false
    void (async () => {
      const [f, u, m, c, d] = await Promise.all([
        settle(getFeatures()),
        settle(getUsage({ since: "24h" })),
        settle(getMailStatus()),
        settle(listLoadboardCreds()),
        settle(getLoadboardDrivers()),
      ])
      if (cancelled) return
      setFeatures(f)
      setUsage(u.data)
      setMail(m)
      setCreds(c)
      setDrivers(d.data)
    })()
    return () => {
      cancelled = true
    }
  }, [version])

  const rows: Row[] = []

  // AI
  {
    const kp = features?.data?.keys_present
    const any = kp ? Object.values(kp).some(Boolean) : false
    const totals = usage?.totals_by_provider ?? {}
    const calls = Object.values(totals).reduce((n, t) => n + (t?.calls ?? 0), 0)
    const usd = Object.values(totals).reduce((n, t) => n + Number(t?.cost_usd ?? 0), 0)
    rows.push({
      id: "ai",
      name: "AI providers",
      state: !features ? "unknown" : features.failed ? "unknown" : any ? "connected" : "not_set_up",
      detail: kp
        ? `Gemini key ${kp.gemini ? "set" : "missing"} · Claude key ${kp.claude ? "set" : "missing"}` +
          (usage ? ` · 24h: ${calls} calls, $${usd.toFixed(2)}` : "")
        : "Status unavailable",
      lastTested: "—",
    })
  }

  // Gmail
  {
    const m = mail?.data
    rows.push({
      id: "gmail",
      name: "Gmail",
      state: !m
        ? "unknown"
        : m.mode === "gmail" && m.sa_configured
          ? "connected"
          : m.sa_configured
            ? "simulated"
            : "not_set_up",
      detail: m
        ? `${m.mode === "gmail" ? m.impersonate || "Gmail" : "Mail is simulated"} · send ${
            m.owner_send_enabled ? "ON" : "OFF"
          } · ${m.sends_today} sent today` + (m.postal_address_set ? "" : " · postal address missing")
        : "Status unavailable",
      lastTested: "—",
    })
  }

  // Load boards
  for (const { key, label } of SRCS) {
    const row = creds?.data?.items.find((r) => r.source === key)
    const drv = drivers ? (drivers[key] as string) : null
    rows.push({
      id: key,
      name: label,
      state: !creds?.data ? "unknown" : row?.configured ? "connected" : "not_set_up",
      detail: creds?.data
        ? `${row?.configured ? (row.username_masked ?? "credential set") : "no credential"}${
            drv ? ` · driver ${drv}` : ""
          }`
        : "Status unavailable",
      lastTested: fmtWhen(row?.last_run_at),
    })
  }

  const current = rows.find((r) => r.id === open)
  const close = (o: boolean) => {
    if (o) return
    setOpen(null)
    setVersion((v) => v + 1) // re-read statuses after any edit
  }

  return (
    <>
      <Panel
        title="Integrations"
        description="Status at a glance. Credentials and tests live behind Edit."
        action={
          <Button size="sm" variant="outline" onClick={() => setOpen("loadsettings")}>
            Load board &amp; rate settings
          </Button>
        }
      >
        <ul className="divide-y divide-border">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 py-2.5">
              <div className="w-40 shrink-0 font-semibold">{r.name}</div>
              <div className="w-28 shrink-0">
                <StateBadge state={r.state} />
              </div>
              <div className="min-w-0 flex-1 truncate text-sm text-muted-foreground" title={r.detail}>
                {r.detail}
              </div>
              <div className="hidden w-48 shrink-0 text-xs text-muted-foreground md:block">
                Last tested: {r.lastTested}
              </div>
              <Button size="sm" variant="outline" onClick={() => setOpen(r.id)}>
                Edit
              </Button>
            </li>
          ))}
        </ul>
      </Panel>

      <Dialog open={open !== null} onOpenChange={close}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              {open === "loadsettings" ? "Load board & rate settings" : `Edit ${current?.name ?? ""}`}
            </DialogTitle>
            <DialogDescription className="sr-only">Edit integration settings</DialogDescription>
          </DialogHeader>
          {open === "ai" && <AiProvidersPanel />}
          {open === "gmail" && (
            <div className="space-y-4">
              <ConnectorsPanel sections={["mail"]} />
              <OwnerSwitches sections={["gmail", "switches"]} />
            </div>
          )}
          {open && SRCS.some((s) => s.key === open) && <LoadboardsPanel src={open as SrcKey} />}
          {open === "loadsettings" && (
            <div className="space-y-4">
              <LoadboardsPanel />
              <AgentBrowserPanel />
              <ConnectorsPanel sections={["sources", "eia"]} />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
