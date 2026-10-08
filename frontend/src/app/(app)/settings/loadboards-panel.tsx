"use client"

/**
 * Load boards panel — per-source credentials + driver dropdown + broker
 * public-page URL allowlist, all stored in the DB vault (no Render env
 * required). Env still wins at read time if the operator sets a
 * `LOADS_<SRC>_DRIVER` or `LOADS_AGENT_KILL=1` on the process — we flag that
 * with a one-line banner so the UI matches reality.
 */

import * as React from "react"
import { toast } from "sonner"
import { Loader2, Plug, Trash2, Save, Check, RefreshCw, Plus, X } from "lucide-react"

import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  clearLoadboardCred,
  getLoadboardDrivers,
  listLoadboardCreds,
  putBrokerPageUrls,
  putLoadboardDrivers,
  setLoadboardCred,
  testLoadboardCred,
  type LoadboardCredRow,
  type LoadboardDriverIn,
  type LoadboardDriverOut,
} from "@/lib/api/connectors"

const SRCS = [
  { key: "dat", label: "DAT" },
  { key: "truckstop", label: "Truckstop" },
  { key: "loadboard123", label: "123Loadboard" },
  { key: "chr", label: "C.H. Robinson" },
] as const

type SrcKey = (typeof SRCS)[number]["key"]
type DriverKey = Exclude<keyof LoadboardDriverOut, "env_override_active" | "agent_kill">

const SRC_TO_DRIVER_KEY: Record<SrcKey, DriverKey> = {
  dat: "dat",
  truckstop: "truckstop",
  loadboard123: "loadboard123",
  chr: "chr",
}

type UrlRow = { label: string; url: string; enabled: boolean }

export function LoadboardsPanel() {
  const [rows, setRows] = React.useState<Record<string, LoadboardCredRow>>({})
  const [drivers, setDrivers] = React.useState<LoadboardDriverOut | null>(null)
  const [urls, setUrls] = React.useState<UrlRow[]>([])
  const [input, setInput] = React.useState<Record<string, { username: string; password: string }>>({})
  const [busy, setBusy] = React.useState<string | null>(null)

  const refresh = React.useCallback(async () => {
    const [creds, drv] = await Promise.all([listLoadboardCreds(), getLoadboardDrivers()])
    if (creds) {
      const byKey: Record<string, LoadboardCredRow> = {}
      for (const r of creds.items) byKey[r.source] = r
      setRows(byKey)
      setUrls(((creds.broker_page_urls ?? []) as unknown as UrlRow[]).map((u) => ({
        label: u.label ?? "",
        url: u.url ?? "",
        enabled: u.enabled !== false,
      })))
    }
    if (drv) setDrivers(drv)
  }, [])

  React.useEffect(() => {
    void refresh().catch(() => {
      // Swallow — initial fetch failure just leaves the panel empty. The
      // Save/Test buttons will still work once the operator interacts.
    })
  }, [refresh])

  async function onSave(src: SrcKey) {
    const v = input[src] ?? { username: "", password: "" }
    if (!v.username || !v.password) {
      toast.error("Username + password required")
      return
    }
    setBusy(`save-${src}`)
    try {
      const r = await setLoadboardCred(src, v.username, v.password)
      if (!r) toast.error("Save failed")
      else toast.success(`${src} saved`)
      setInput((s) => ({ ...s, [src]: { username: "", password: "" } }))
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onTest(src: SrcKey) {
    setBusy(`test-${src}`)
    try {
      const r = await testLoadboardCred(src)
      if (!r) toast.error("Test failed")
      else if (r.configured) toast.success(`${src}: credential OK (${r.username_masked ?? "—"})`)
      else toast.error(`${src}: ${r.reason ?? "not configured"}`)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onClear(src: SrcKey) {
    if (!confirm(`Clear stored credential for ${src}?`)) return
    setBusy(`clear-${src}`)
    try {
      await clearLoadboardCred(src)
      toast.success(`${src} cleared`)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onDriverChange(src: SrcKey, value: "off" | "api" | "agent") {
    setBusy(`drv-${src}`)
    try {
      const key = SRC_TO_DRIVER_KEY[src]
      const r = await putLoadboardDrivers({ [key]: value } as Partial<LoadboardDriverIn>)
      if (!r) toast.error("Driver update failed")
      else toast.success(`${src} → ${value}`)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onKillToggle(enabled: boolean) {
    setBusy("kill")
    try {
      const r = await putLoadboardDrivers({ agent_kill: enabled ? "on" : "off" })
      if (!r) toast.error("Kill switch update failed")
      else toast.success(`Agent kill switch: ${enabled ? "ON" : "OFF"}`)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onSaveUrls() {
    setBusy("urls")
    try {
      const clean = urls.filter((u) => u.url.trim().length > 0)
      const r = await putBrokerPageUrls(clean)
      if (!r) toast.error("URL save failed")
      else toast.success(`${clean.length} URL${clean.length === 1 ? "" : "s"} saved`)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel
      title="Load boards"
      description="Paste each board's login once; it stores encrypted in the DB vault (no Render env needed). Pick a driver: off, api (official API, needs vendor key), or agent (headless browser login). The agent kill switch disables every agent run instantly."
    >
      {drivers?.env_override_active && (
        <p className="mb-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          An env var (LOADS_*_DRIVER or LOADS_AGENT_KILL) is set on the process — it wins over the DB values below.
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 text-left">Source</th>
              <th className="py-1 pr-3 text-left">Driver</th>
              <th className="py-1 pr-3 text-left">Credential</th>
              <th className="py-1 pr-3 text-left">Last run</th>
              <th className="py-1 pr-3 text-left">Set / rotate</th>
              <th className="py-1 pr-3" />
            </tr>
          </thead>
          <tbody>
            {SRCS.map(({ key, label }) => {
              const row = rows[key]
              const inp = input[key] ?? { username: "", password: "" }
              const driverVal = drivers ? (drivers[SRC_TO_DRIVER_KEY[key]] as string) : "off"
              return (
                <tr key={key} className="border-t align-top">
                  <td className="py-2 pr-3 font-medium">{label}</td>
                  <td className="py-2 pr-3">
                    <select
                      value={driverVal}
                      disabled={busy === `drv-${key}`}
                      onChange={(e) =>
                        onDriverChange(key, e.target.value as "off" | "api" | "agent")
                      }
                      className="rounded border bg-background px-2 py-1 text-sm"
                    >
                      <option value="off">off</option>
                      <option value="api">api</option>
                      <option value="agent">agent</option>
                    </select>
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    {row?.configured ? (
                      <span className="inline-flex items-center gap-1 rounded bg-green-500/10 px-2 py-0.5 text-green-700">
                        <Check className="h-3 w-3" /> {row.username_masked ?? "set"}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">
                        {row?.reason ?? "not set"}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted-foreground">
                    {row?.last_run_status ?? "—"}
                    {row?.last_run_at ? ` · ${new Date(row.last_run_at).toLocaleString()}` : ""}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-col gap-1">
                      <Input
                        placeholder="username"
                        value={inp.username}
                        onChange={(e) =>
                          setInput((s) => ({
                            ...s,
                            [key]: { ...(s[key] ?? { username: "", password: "" }), username: e.target.value },
                          }))
                        }
                        className="h-7 text-xs"
                      />
                      <Input
                        placeholder="password"
                        type="password"
                        value={inp.password}
                        onChange={(e) =>
                          setInput((s) => ({
                            ...s,
                            [key]: { ...(s[key] ?? { username: "", password: "" }), password: e.target.value },
                          }))
                        }
                        className="h-7 text-xs"
                      />
                    </div>
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap gap-1">
                      <Button size="sm" onClick={() => onSave(key)} disabled={busy === `save-${key}`}>
                        {busy === `save-${key}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                        Save
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => onTest(key)} disabled={busy === `test-${key}`}>
                        {busy === `test-${key}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plug className="h-3 w-3" />}
                        Test
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onClear(key)}
                        disabled={busy === `clear-${key}` || !row?.configured}
                      >
                        {busy === `clear-${key}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                        Clear
                      </Button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-5 flex items-center gap-3">
        <Label>Agent kill switch (disables every agent run)</Label>
        <Button
          size="sm"
          variant={drivers?.agent_kill === "on" ? "destructive" : "outline"}
          onClick={() => onKillToggle(drivers?.agent_kill !== "on")}
          disabled={busy === "kill"}
        >
          {busy === "kill" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
          {drivers?.agent_kill === "on" ? "KILLED (click to resume)" : "Kill all agent runs"}
        </Button>
      </div>

      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold">Public broker-page URLs</h3>
            <p className="text-xs text-muted-foreground">
              Operator-managed allowlist for the <code>ai_page</code> source. One agent run per enabled URL per cycle.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                setUrls((u) => [...u, { label: "", url: "https://", enabled: true }])
              }
            >
              <Plus className="h-3 w-3" /> Add URL
            </Button>
            <Button size="sm" onClick={onSaveUrls} disabled={busy === "urls"}>
              {busy === "urls" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              Save URLs
            </Button>
          </div>
        </div>
        {urls.length === 0 ? (
          <p className="text-sm text-muted-foreground">No public broker pages configured yet.</p>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 text-left">Label</th>
                <th className="py-1 pr-3 text-left">URL</th>
                <th className="py-1 pr-3 text-left">Enabled</th>
                <th className="py-1 pr-3" />
              </tr>
            </thead>
            <tbody>
              {urls.map((u, i) => (
                <tr key={i} className="border-t">
                  <td className="py-1 pr-3">
                    <Input
                      value={u.label}
                      onChange={(e) =>
                        setUrls((rs) => rs.map((r, j) => (j === i ? { ...r, label: e.target.value } : r)))
                      }
                      className="h-7 text-xs"
                    />
                  </td>
                  <td className="py-1 pr-3">
                    <Input
                      value={u.url}
                      onChange={(e) =>
                        setUrls((rs) => rs.map((r, j) => (j === i ? { ...r, url: e.target.value } : r)))
                      }
                      className="h-7 text-xs"
                    />
                  </td>
                  <td className="py-1 pr-3">
                    <input
                      type="checkbox"
                      checked={u.enabled}
                      onChange={(e) =>
                        setUrls((rs) => rs.map((r, j) => (j === i ? { ...r, enabled: e.target.checked } : r)))
                      }
                    />
                  </td>
                  <td className="py-1 pr-3 text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setUrls((rs) => rs.filter((_, j) => j !== i))}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  )
}
