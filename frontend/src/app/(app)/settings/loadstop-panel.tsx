/**
 * LoadStop (TMS) settings row — connector for the client's real TMS.
 *
 * Shape matches the backend ``/api/loadboards/loadstop`` surface:
 *   GET → state (driver, status, email_masked, toggles, last_sync)
 *   PATCH → write-only credentials + driver + toggles
 *   POST /test → attempt login with stored creds
 *
 * The password is write-only — the GET never returns it. The ``agent``
 * driver mode deliberately isn't offered (LoadStop uses httpx, not
 * Playwright). All four import toggles default to true; they're a
 * no-op until the driver flips to ``api``.
 */
"use client"

import * as React from "react"
import { toast } from "sonner"
import { Loader2, Save, Plug } from "lucide-react"

import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"

type Toggles = { fleet: boolean; customers: boolean; loads: boolean; rates: boolean }
type State = {
  driver: "off" | "api"
  status: string
  email_set: boolean
  email_masked: string | null
  has_session: boolean
  last_login_at: string | null
  toggles: Toggles
  last_sync: Record<string, string | null>
  dat_connected: boolean
}

const EMPTY: State = {
  driver: "off",
  status: "not_configured",
  email_set: false,
  email_masked: null,
  has_session: false,
  last_login_at: null,
  toggles: { fleet: true, customers: true, loads: true, rates: true },
  last_sync: {},
  dat_connected: false,
}

export function LoadstopPanel() {
  const [state, setState] = React.useState<State>(EMPTY)
  const [loaded, setLoaded] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [testing, setTesting] = React.useState(false)
  const [email, setEmail] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [oneTimeCode, setOneTimeCode] = React.useState("")

  const refresh = React.useCallback(async () => {
    try {
      const res = await fetch("/api/loadboards/loadstop", { credentials: "include" })
      if (!res.ok) throw new Error(`GET ${res.status}`)
      const data = (await res.json()) as State
      setState(data)
    } catch (err) {
      // Soft-fail: the panel still renders in the ``not_configured`` state.
      console.warn("loadstop panel load failed", err)
    } finally {
      setLoaded(true)
    }
  }, [])

  React.useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const res = await fetch("/api/loadboards/loadstop", { credentials: "include" })
        if (!res.ok) throw new Error(`GET ${res.status}`)
        const data = (await res.json()) as State
        if (!cancelled) setState(data)
      } catch (err) {
        console.warn("loadstop panel load failed", err)
      } finally {
        if (!cancelled) setLoaded(true)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const body: Record<string, unknown> = { toggles: state.toggles, driver: state.driver }
      if (email && password) {
        body.email = email
        body.password = password
        if (oneTimeCode) body.one_time_code = oneTimeCode
      }
      const res = await fetch("/api/loadboards/loadstop", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`PATCH ${res.status}`)
      const data = (await res.json()) as State
      setState(data)
      setPassword("")
      setOneTimeCode("")
      toast.success("Saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed")
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    try {
      const res = await fetch("/api/loadboards/loadstop/test", { method: "POST", credentials: "include" })
      const data = (await res.json()) as { status: string; detail?: string }
      if (data.status === "ok") toast.success("Connected")
      else if (data.status === "not_configured") toast.message("Save email + password first")
      else if (data.status === "needs_code") toast.warning("One-time code required")
      else if (data.status === "bad_credentials") toast.error("Bad credentials")
      else toast.error(data.detail ?? data.status)
      await refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Test failed")
    } finally {
      setTesting(false)
    }
  }

  const flip = (key: keyof Toggles) => (v: boolean) =>
    setState((s) => ({ ...s, toggles: { ...s.toggles, [key]: v } }))

  return (
    <Panel
      title="LoadStop (TMS)"
      description="fleet · customers · DAT loads · rates — strictly read-only; writes to LoadStop are blocked in code"
    >
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ls-email">Email</Label>
            <Input
              id="ls-email"
              type="email"
              placeholder={state.email_masked ?? "owner@…"}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="off"
            />
            {state.email_set && !email && (
              <p className="text-xs text-muted-foreground">Stored: {state.email_masked}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ls-pw">Password</Label>
            <Input
              id="ls-pw"
              type="password"
              placeholder={state.email_set ? "•••••• (stored)" : "write-only"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
        </div>

        {state.status === "needs_code" && (
          <div className="space-y-1.5">
            <Label htmlFor="ls-otp">One-time code</Label>
            <Input
              id="ls-otp"
              inputMode="numeric"
              value={oneTimeCode}
              onChange={(e) => setOneTimeCode(e.target.value)}
            />
          </div>
        )}

        <div className="flex items-center gap-3">
          <Label htmlFor="ls-driver" className="min-w-20">Driver</Label>
          <select
            id="ls-driver"
            className="rounded border border-border bg-background px-2 py-1 text-sm"
            value={state.driver}
            onChange={(e) => setState((s) => ({ ...s, driver: e.target.value as "off" | "api" }))}
          >
            <option value="off">off</option>
            <option value="api">api</option>
          </select>
          <span className="text-xs text-muted-foreground">
            session: {state.has_session ? "stored" : "none"}
            {state.last_login_at ? ` · last login ${state.last_login_at.slice(0, 16)}` : ""}
          </span>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          {(["fleet", "customers", "loads", "rates"] as const).map((k) => (
            <div key={k} className="flex items-center justify-between rounded border border-border px-3 py-2">
              <div>
                <Label className="capitalize">{k}</Label>
                <div className="text-xs text-muted-foreground">
                  last sync: {state.last_sync?.[k] ?? "—"}
                </div>
              </div>
              <Switch checked={state.toggles[k]} onCheckedChange={flip(k)} />
            </div>
          ))}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
          <Button variant="outline" onClick={test} disabled={!loaded || testing}>
            {testing ? <Loader2 className="animate-spin" /> : <Plug />} Test
          </Button>
          <Button onClick={save} disabled={!loaded || saving}>
            {saving ? <Loader2 className="animate-spin" /> : <Save />} Save
          </Button>
        </div>
      </div>
    </Panel>
  )
}
