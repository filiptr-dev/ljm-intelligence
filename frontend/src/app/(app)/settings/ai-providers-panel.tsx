"use client"

/**
 * AI providers section for the Owner Settings page.
 *
 * Self-contained: fetches `/ai/features` and `/ai/usage?since=24h` through the
 * typed openapi-fetch client, saves the matrix via `PUT /settings` with the
 * `ai_features` blob. Kept apart from the big settings `save()` flow so this
 * doesn't need to merge into the omnibus patch body — one owner, two moving
 * parts, no shared mutable state.
 */

import * as React from "react"
import { toast } from "sonner"
import { Save } from "lucide-react"

import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api/client"
import { getFeatures, getUsage, type FeaturesResponse, type UsageResponse } from "@/lib/api/ai"

const FEATURE_LABELS: Record<string, string> = {
  email_drafts: "Email drafts",
  inbox_analysis: "Inbox analysis",
  lead_scoring: "Lead scoring",
  enrichment_extractor: "Website extractor",
  shipper_discovery: "Shipper discovery",
}

const PROVIDERS = ["gemini", "claude"] as const
type Provider = (typeof PROVIDERS)[number]

function fmtMoney(v: string | number | undefined): string {
  if (v === undefined) return "$0.00"
  const n = typeof v === "string" ? Number(v) : v
  return Number.isFinite(n) ? `$${n.toFixed(4)}` : "$0.00"
}

export function AiProvidersPanel() {
  const [features, setFeatures] = React.useState<FeaturesResponse | null>(null)
  const [usage, setUsage] = React.useState<UsageResponse | null>(null)
  const [matrix, setMatrix] = React.useState<Record<string, { provider: string; model: string }>>({})
  const [saving, setSaving] = React.useState(false)

  const refresh = React.useCallback(async () => {
    try {
      const [f, u] = await Promise.all([getFeatures(), getUsage({ since: "24h" })])
      setFeatures(f)
      setUsage(u)
      const next: Record<string, { provider: string; model: string }> = {}
      for (const [feature, choice] of Object.entries(f.features)) {
        next[feature] = { provider: choice.provider, model: choice.model }
      }
      setMatrix(next)
    } catch (e) {
      toast.error("Couldn't load AI providers", { description: String(e) })
    }
  }, [])

  React.useEffect(() => {
    // Fire the fetch imperatively; state updates land in the async callback,
    // not in the effect body itself (lint rule: no sync setState in effects).
    let active = true
    void (async () => {
      if (!active) return
      await refresh()
    })()
    return () => {
      active = false
    }
  }, [refresh])

  const save = async () => {
    setSaving(true)
    try {
      // PUT /settings with just the ai_features blob. The settings validator
      // rejects an invalid (provider, model) pair with 422 — surface verbatim.
      const res = await api.PUT("/settings", { body: { ai_features: matrix } })
      if (res.error) throw new Error(JSON.stringify(res.error))
      toast.success("AI providers saved")
      await refresh()
    } catch (e) {
      toast.error("Couldn't save", { description: String(e) })
    } finally {
      setSaving(false)
    }
  }

  const keysPresent = features?.keys_present ?? { gemini: false, claude: false }

  return (
    <Panel
      title="AI providers"
      description="Pick which model handles each AI feature. Grounded discovery stays on Gemini for now (Claude web search comes later)."
    >
      {!features ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          <div className="mb-3 grid gap-2 sm:grid-cols-2">
            {PROVIDERS.map((p) => {
              const totals = usage?.totals_by_provider?.[p]
              const label = p === "gemini" ? "Gemini" : "Claude"
              return (
                <div key={p} className="rounded-sm border border-border bg-background p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{label} · last 24h</span>
                    {!keysPresent[p] ? (
                      <span className="rounded-sm bg-destructive/10 px-1.5 py-0.5 text-[0.65rem] font-bold tracking-wider text-destructive uppercase">
                        No key
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                    <div>
                      <div className="font-mono text-sm text-foreground">{totals?.calls ?? 0}</div>
                      <div>calls</div>
                    </div>
                    <div>
                      <div className="font-mono text-sm text-foreground">{totals?.tokens ?? 0}</div>
                      <div>tokens</div>
                    </div>
                    <div>
                      <div className="font-mono text-sm text-foreground">{fmtMoney(totals?.cost_usd)}</div>
                      <div>USD</div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          <div className="space-y-2">
            {Object.entries(FEATURE_LABELS).map(([feature, label]) => {
              const choice = matrix[feature] ?? features.features[feature]
              if (!choice) return null
              const groundedOnly = feature === "shipper_discovery"
              const providerOptions = groundedOnly ? (["gemini"] as const) : PROVIDERS
              const allowed = features.allowed_models[choice.provider as Provider] ?? []
              const noKey = !keysPresent[choice.provider as Provider]
              return (
                <div key={feature} className="grid grid-cols-[1fr_auto_auto] items-center gap-2">
                  <div className="text-sm">
                    <div className="font-medium">{label}</div>
                    {groundedOnly ? (
                      <div className="text-xs text-muted-foreground">
                        Grounded search — Gemini only for now.
                      </div>
                    ) : null}
                    {noKey ? (
                      <div className="text-xs text-destructive">No API key configured for {choice.provider}.</div>
                    ) : null}
                  </div>
                  <Select
                    value={choice.provider}
                    onValueChange={(v) => {
                      if (!v) return
                      const nextAllowed = features.allowed_models[v as Provider] ?? []
                      setMatrix((m) => ({
                        ...m,
                        [feature]: { provider: v, model: nextAllowed[0] ?? choice.model },
                      }))
                    }}
                  >
                    <SelectTrigger className="w-[120px]">
                      <SelectValue>{choice.provider}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {providerOptions.map((p) => (
                        <SelectItem key={p} value={p}>
                          {p}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={choice.model}
                    onValueChange={(v) => {
                      if (!v) return
                      setMatrix((m) => ({ ...m, [feature]: { provider: choice.provider, model: v } }))
                    }}
                  >
                    <SelectTrigger className="w-[220px]">
                      <SelectValue>{choice.model}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {allowed.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )
            })}
          </div>

          <div className="mt-4 flex items-center justify-end gap-2 border-t border-border pt-3">
            <Button onClick={save} disabled={saving} className="font-semibold">
              <Save /> {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </>
      )}
    </Panel>
  )
}
