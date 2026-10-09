"use client"

/**
 * Agent-browser (headless LLM crawler sidecar) — URL + token lives in the
 * DB vault, Env still wins at read time (``AGENT_BROWSER_URL`` /
 * ``AGENT_BROWSER_TOKEN``). Test button calls the sidecar's ``/health``
 * through the backend so the operator can prove reachability without
 * exposing the token to the browser.
 */

import * as React from "react"
import { toast } from "sonner"
import { Check, Loader2, Plug, Save, Trash2 } from "lucide-react"

import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  clearAgentBrowser,
  getAgentBrowser,
  putAgentBrowser,
  testAgentBrowser,
  type AgentBrowserState,
  type AgentBrowserTestResult,
} from "@/lib/api/connectors"

export function AgentBrowserPanel() {
  const [state, setState] = React.useState<AgentBrowserState | null>(null)
  const [url, setUrl] = React.useState("")
  const [token, setToken] = React.useState("")
  const [busy, setBusy] = React.useState<string | null>(null)
  const [testResult, setTestResult] = React.useState<AgentBrowserTestResult | null>(null)

  const refresh = React.useCallback(async () => {
    const r = await getAgentBrowser()
    if (r) {
      setState(r)
      setUrl(r.url ?? "")
    }
  }, [])

  React.useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const r = await getAgentBrowser()
        if (!cancelled && r) {
          setState(r)
          setUrl(r.url ?? "")
        }
      } catch {
        // Panel stays empty on first-boot fetch failure; Save/Test still work.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  async function onSave() {
    const patch: { url?: string; token?: string } = {}
    if (url !== (state?.url ?? "")) patch.url = url.trim()
    if (token) patch.token = token
    if (Object.keys(patch).length === 0) {
      toast.message("Nothing to save")
      return
    }
    setBusy("save")
    try {
      const r = await putAgentBrowser(patch)
      if (!r) toast.error("Save failed")
      else toast.success("Agent-browser updated")
      setToken("")
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onClear() {
    if (!confirm("Clear sidecar URL and token? Env values (if set) will still apply.")) return
    setBusy("clear")
    try {
      await clearAgentBrowser()
      toast.success("Cleared")
      setToken("")
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onTest() {
    setBusy("test")
    setTestResult(null)
    try {
      const r = await testAgentBrowser()
      setTestResult(r)
      if (!r) toast.error("Test failed")
      else if (r.ok) toast.success(`Sidecar OK (${r.latency_ms ?? "?"} ms, auth=${r.auth ?? "?"})`)
      else toast.error(`Sidecar unreachable: ${r.reason ?? "unknown"}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel
      title="Agent-browser (headless crawler sidecar)"
      description="Playwright/Chromium service the loads AI agent talks to. URL stored on the settings row; token encrypted in the DB vault."
    >
      {(state?.env_url_override || state?.env_token_override) && (
        <p className="mb-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Env var active:
          {state.env_url_override ? " AGENT_BROWSER_URL" : ""}
          {state.env_url_override && state.env_token_override ? " +" : ""}
          {state.env_token_override ? " AGENT_BROWSER_TOKEN" : ""}
          — overrides the DB value below.
        </p>
      )}

      <div className="grid gap-3">
        <div>
          <Label className="text-xs">Sidecar URL</Label>
          <Input
            value={url}
            placeholder="https://ljm-intelligence-agent-browser.onrender.com"
            onChange={(e) => setUrl(e.target.value)}
            className="h-8 text-sm"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Source:{" "}
            <span className="font-mono">{state?.url_source ?? "unset"}</span>
            {state?.url ? ` · effective ${state.url}` : ""}
          </p>
        </div>

        <div>
          <Label className="text-xs">Shared secret (X-Agent-Token)</Label>
          <Input
            type="password"
            value={token}
            placeholder={
              state?.token_set
                ? "configured — paste a new value to rotate"
                : "paste the sidecar's AGENT_BROWSER_TOKEN"
            }
            onChange={(e) => setToken(e.target.value)}
            className="h-8 text-sm"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {state?.token_set ? (
              <span className="inline-flex items-center gap-1 text-green-700">
                <Check className="h-3 w-3" /> set (source: {state?.token_source})
              </span>
            ) : (
              <span>not set — sidecar will refuse every tool call</span>
            )}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onSave} disabled={busy === "save"}>
            {busy === "save" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Save
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onTest}
            disabled={busy === "test" || !state?.url}
          >
            {busy === "test" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plug className="h-3 w-3" />}
            Test
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onClear}
            disabled={busy === "clear" || !(state?.url || state?.token_set)}
          >
            {busy === "clear" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
            Clear
          </Button>
        </div>

        {testResult && (
          <pre className="rounded border bg-muted/40 p-2 text-xs">
            {JSON.stringify(testResult, null, 2)}
          </pre>
        )}
      </div>
    </Panel>
  )
}
