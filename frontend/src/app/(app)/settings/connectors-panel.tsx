"use client"

/**
 * Connectors panel — mail connection + load sources.
 *
 * Both plans ship real adapters that default OFF. The UI shows status and
 * lets the owner "Test connection" / "Force refresh" once Render env is set.
 * Secrets are never collected here — Render-only, per plan.
 */

import * as React from "react"
import { toast } from "sonner"
import { Check, Loader2, Mail, Plug, RefreshCw, X } from "lucide-react"

import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  getMailStatus,
  listLoadSources,
  postMailTestSend,
  postMailDisconnect,
  refreshLoadSource,
  testLoadSource,
  type LoadSourceRow,
  type MailStatus,
} from "@/lib/api/connectors"

const SOURCE_LABELS: Record<string, string> = {
  ai_page: "AI pages",
  paste: "Paste",
  dat: "DAT",
  chr: "C.H. Robinson",
  loadboard123: "123Loadboard",
  truckstop: "Truckstop",
}

/**
 * Translate the API's machine-readable ``reason`` string into a plain
 * sentence for an operator. The raw string stays on ``title={s.reason}`` so
 * support/logs can still see the exact env var or state. Keep this UI-side
 * only — the API, runbook, and log search still want ``missing_env:FOO``.
 */
function prettyReason(reason: string | null | undefined): string {
  if (!reason) return "Disabled."
  if (reason.startsWith("missing_env:")) {
    return "Needs credentials — see the connector runbook."
  }
  if (reason === "circuit_open") {
    return "Paused — vendor had too many errors; retrying in a few minutes."
  }
  return "Disabled."
}

export function ConnectorsPanel() {
  const [mail, setMail] = React.useState<MailStatus | null>(null)
  const [sources, setSources] = React.useState<LoadSourceRow[]>([])
  const [testTo, setTestTo] = React.useState("")
  const [busy, setBusy] = React.useState<string | null>(null)

  const refresh = React.useCallback(async () => {
    const [m, s] = await Promise.all([getMailStatus(), listLoadSources()])
    setMail(m)
    setSources(s)
  }, [])

  React.useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [m, s] = await Promise.all([getMailStatus(), listLoadSources()])
        if (cancelled) return
        setMail(m)
        setSources(s)
      } catch {
        // swallow — the panel just stays empty on fetch failure
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  async function onTestMail() {
    if (!testTo) return
    setBusy("mail-test")
    try {
      const r = await postMailTestSend(testTo)
      if (!r) {
        toast.error("Test send failed")
      } else if (r.ok) {
        toast.success(`${r.mode === "real" ? "Sent" : "Simulated"} — ${r.message_id ?? "(no id)"}`)
      } else {
        toast.error(r.reason ?? "Test send failed")
      }
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onDisconnect() {
    setBusy("mail-disconnect")
    try {
      await postMailDisconnect()
      toast.success("Mail disconnected (simulated)")
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onTestSource(kind: string) {
    setBusy(`src-test-${kind}`)
    try {
      const r = await testLoadSource(kind)
      if (!r) {
        toast.error("Test failed")
      } else if (r.ok) {
        toast.success(`${kind}: ok (${r.latency_ms} ms)`)
      } else {
        toast.error(`${kind}: ${r.reason ?? "fail"}`)
      }
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onRefreshSource(kind: string) {
    setBusy(`src-refresh-${kind}`)
    try {
      const r = await refreshLoadSource(kind)
      if (!r) {
        toast.error("Refresh failed")
      } else {
        toast.message(`${kind}: ${r.status} — ${r.inserted} new / ${r.skipped} dupe`)
      }
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  const mailMode = mail?.mode ?? "simulated"
  const mailOk = mailMode === "gmail" && mail?.sa_configured && mail?.postal_address_set

  return (
    <>
      <Panel
        title="Mail connection"
        description="Default is simulated. Add your Google Workspace credentials in Render, then click Test connection — it switches to live Gmail once the credentials verify."
      >
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-sm">
            <Mail className="h-4 w-4" />
            <span
              className={
                mailOk
                  ? "rounded px-2 py-0.5 bg-green-500/10 text-green-700"
                  : "rounded px-2 py-0.5 bg-amber-500/10 text-amber-700"
              }
            >
              {mailOk ? `Gmail · ${mail?.impersonate}` : "Simulated"}
            </span>
          </div>
          {mail?.sa_fingerprint && (
            <span className="text-xs text-muted-foreground">SA #{mail.sa_fingerprint}</span>
          )}
          <span className="text-xs text-muted-foreground">
            Sends today: {mail?.sends_today ?? 0}
          </span>
          {!mail?.postal_address_set && (
            <span className="text-xs text-red-600">
              Postal address missing — add it in Render before sending real email.
            </span>
          )}
          {mail?.reason && (
            <span className="text-xs text-red-600" title={mail.reason}>
              {prettyReason(mail.reason)}
            </span>
          )}
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <div className="flex min-w-0 flex-col gap-1">
            <Label htmlFor="mail-test-to">Send a test to</Label>
            <Input
              id="mail-test-to"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              placeholder="owner@ljm-demo.local"
              className="min-w-[220px]"
            />
          </div>
          <Button onClick={onTestMail} disabled={busy === "mail-test" || !testTo}>
            {busy === "mail-test" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
            Test connection
          </Button>
          <Button variant="outline" onClick={onDisconnect} disabled={busy === "mail-disconnect"}>
            Disconnect
          </Button>
        </div>
      </Panel>

      <Panel
        title="Load sources"
        description="Every connector starts off. Add the vendor's credentials in Render, then click Test connection. We never collect credentials in the browser."
      >
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 text-left">Source</th>
                <th className="py-1 pr-3 text-left">Status</th>
                <th className="py-1 pr-3 text-left">Last verified</th>
                <th className="py-1 pr-3" />
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.kind} className="border-t">
                  <td className="py-2 pr-3 font-medium">{SOURCE_LABELS[s.kind] ?? s.kind}</td>
                  <td className="py-2 pr-3">
                    {s.enabled ? (
                      <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 bg-green-500/10 text-green-700">
                        <Check className="h-3 w-3" /> enabled
                      </span>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 rounded px-2 py-0.5 bg-muted"
                        title={s.reason ?? ""}
                      >
                        <X className="h-3 w-3" /> {prettyReason(s.reason)}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted-foreground">
                    {s.last_verified_at ? new Date(s.last_verified_at).toLocaleString() : "—"}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap items-center justify-end gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onTestSource(s.kind)}
                        disabled={busy === `src-test-${s.kind}`}
                      >
                        {busy === `src-test-${s.kind}` ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Plug className="h-3 w-3" />
                        )}
                        Test
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onRefreshSource(s.kind)}
                        disabled={!s.enabled || busy === `src-refresh-${s.kind}`}
                      >
                        {busy === `src-refresh-${s.kind}` ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <RefreshCw className="h-3 w-3" />
                        )}
                        Refresh
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}
