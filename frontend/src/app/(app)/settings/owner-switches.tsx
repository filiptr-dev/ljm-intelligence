"use client"

import { useState, useTransition } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api/client"
import * as inbox from "@/lib/api/inbox"

/**
 * Owner switches + forget-contact + Gmail connect panel.
 *
 * - Inbox source (simulated ↔ gmail): one-click toggle (POST /settings/connectors/gmail/inbox-source).
 * - Send via Gmail: default OFF; ON requires type-to-confirm 'CONFIRM' per plan.
 * - Gmail SA JSON upload — paste JSON, hits POST /settings/connectors/gmail (stores in CredentialVault).
 * - Forget contact — purges every message/insight for an address, writes an audit row.
 */
export type OwnerSection = "gmail" | "switches" | "forget"

/** `sections` lets the Settings page mount the Gmail/switches cards inside the
 *  Gmail Edit dialog and the Forget-contact card on the page, same handlers. */
export function OwnerSwitches({ sections = ["gmail", "switches", "forget"] }: { sections?: OwnerSection[] }) {
  const [saJson, setSaJson] = useState("")
  const [impersonate, setImpersonate] = useState("")
  const [email, setEmail] = useState("")
  const [confirm, setConfirm] = useState("")
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [pending, startTransition] = useTransition()

  function flashOk(s: string) { setMsg(s); setErr(null) }
  function flashErr(s: string) { setErr(s); setMsg(null) }

  function connectGmail() {
    startTransition(async () => {
      const res = await api.POST("/settings/connectors/gmail", { body: { sa_json: saJson, impersonate } })
      if (res.data?.ok) flashOk(`Gmail credential stored. Fingerprint ${res.data.fingerprint ?? "—"}.`)
      else flashErr(res.data?.reason ?? "Store failed")
    })
  }
  function revokeGmail() {
    startTransition(async () => {
      const res = await api.DELETE("/settings/connectors/gmail", {})
      if (res.data?.ok) flashOk("Gmail credential revoked.")
      else flashErr("Revoke failed")
    })
  }
  function sendSwitch(enabled: boolean) {
    startTransition(async () => {
      const res = await api.POST("/settings/connectors/gmail/send-switch", { body: { enabled, confirm: enabled ? confirm : "" } })
      if (res.data?.ok) flashOk(`Send via Gmail is now ${enabled ? "ON" : "OFF"}.`)
      else flashErr("Switch failed — did you type CONFIRM?")
    })
  }
  function inboxSourceSwitch(enabled: boolean) {
    startTransition(async () => {
      const res = await api.POST("/settings/connectors/gmail/inbox-source", { body: { enabled, confirm: "" } })
      if (res.data?.ok) flashOk(`Inbox source: ${enabled ? "gmail" : "simulated"}.`)
      else flashErr("Switch failed")
    })
  }
  function forget() {
    startTransition(async () => {
      try {
        const r = await inbox.forgetContact(email)
        if (r.ok) flashOk(`Deleted ${r.messages_deleted} message(s) and ${r.insights_deleted} insight(s) for ${r.email}.`)
        else flashErr(r.reason ?? "Delete failed")
      } catch (e) {
        flashErr(e instanceof Error ? e.message : "Delete failed")
      }
    })
  }

  return (
    <div className="space-y-4">
      {sections.includes("gmail") && (
      <div className="rounded-sm border border-border bg-card p-3">
        <div className="mb-2 font-semibold">Gmail connector (owner-only)</div>
        <textarea
          placeholder="Paste Google service-account JSON here…"
          value={saJson}
          onChange={(e) => setSaJson(e.target.value)}
          rows={5}
          className="w-full rounded-sm border border-border bg-background p-2 font-mono text-xs"
        />
        <Input placeholder="Impersonate (owner@ljminternational.com)" value={impersonate} onChange={(e) => setImpersonate(e.target.value)} className="mt-2" />
        <div className="mt-2 flex gap-2">
          <Button onClick={connectGmail} disabled={pending || saJson.length < 20}>Store credential</Button>
          <Button variant="outline" onClick={revokeGmail} disabled={pending}>Revoke</Button>
        </div>
      </div>
      )}

      {sections.includes("switches") && (
      <div className="rounded-sm border border-border bg-card p-3">
        <div className="mb-2 font-semibold">Owner switches</div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => inboxSourceSwitch(false)} disabled={pending}>Inbox: simulated</Button>
          <Button variant="outline" onClick={() => inboxSourceSwitch(true)} disabled={pending}>Inbox: gmail</Button>
          <Button variant="outline" onClick={() => sendSwitch(false)} disabled={pending}>Send via Gmail: OFF</Button>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Input placeholder="Type CONFIRM to enable sending" value={confirm} onChange={(e) => setConfirm(e.target.value)} className="max-w-xs" />
          <Button onClick={() => sendSwitch(true)} disabled={pending || confirm !== "CONFIRM"}>Send via Gmail: ON</Button>
        </div>
      </div>
      )}

      {sections.includes("forget") && (
      <div className="rounded-sm border border-border bg-card p-3">
        <div className="mb-2 font-semibold">Forget contact (GDPR-style)</div>
        <div className="flex items-center gap-2">
          <Input placeholder="user@domain.com" value={email} onChange={(e) => setEmail(e.target.value)} className="max-w-xs" />
          <Button onClick={forget} disabled={pending || !email.includes("@")}>Forget this contact</Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Deletes every stored message + insight for that address and writes an audit row.
        </p>
      </div>
      )}

      {msg ? <div className="rounded-sm border border-good/40 bg-good/5 p-3 text-sm text-good">{msg}</div> : null}
      {err ? <div className="rounded-sm border border-bad/40 bg-bad/5 p-3 text-sm text-bad">{err}</div> : null}
    </div>
  )
}
