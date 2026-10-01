"use client"

/**
 * AI providers section for the Owner Settings page.
 *
 * Self-contained: fetches `/ai/features` and `/ai/usage?since=24h` through the
 * typed openapi-fetch client, saves the matrix via `PUT /settings` with the
 * `ai_features` blob. Kept apart from the big settings `save()` flow so this
 * doesn't need to merge into the omnibus patch body — one owner, two moving
 * parts, no shared mutable state.
 *
 * Layout note: this panel lives in the right-hand 380px sidebar on xl+ and
 * stretches full width below that. Every row is a vertical stack
 * (label on top, provider+model selects beneath) so long model ids never
 * elbow the panel wider than its container — `min-w-0` on every grid cell is
 * the load-bearing bit (flex/grid children default to `min-width: auto`,
 * which is what breaks overflow in a narrow column).
 */

import * as React from "react"
import { toast } from "sonner"
import { Save } from "lucide-react"

import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api/client"
import { getFeatures, getUsage, type FeaturesResponse, type UsageResponse } from "@/lib/api/ai"

const FEATURE_LABELS: Record<string, { label: string; blurb: string }> = {
  email_drafts: {
    label: "Email drafts",
    blurb: "Writes the outreach body against your tone and template.",
  },
  inbox_analysis: {
    label: "Inbox analysis",
    blurb: "Classifies replies and extracts intent + sentiment.",
  },
  lead_scoring: {
    label: "Lead scoring",
    blurb: "Rates a company's fit from enrichment signals.",
  },
  enrichment_extractor: {
    label: "Website extractor",
    blurb: "Pulls contact + company facts from a crawled page.",
  },
  shipper_discovery: {
    label: "Shipper discovery",
    blurb: "Grounded search for new brokers — Gemini only for now.",
  },
}

const PROVIDERS = ["gemini", "claude"] as const
type Provider = (typeof PROVIDERS)[number]

const PROVIDER_LABEL: Record<Provider, string> = {
  gemini: "Gemini",
  claude: "Claude",
}

function fmtMoney(v: string | number | undefined): string {
  if (v === undefined) return "$0.00"
  const n = typeof v === "string" ? Number(v) : v
  return Number.isFinite(n) ? `$${n.toFixed(4)}` : "$0.00"
}

function fmtNum(n: number | undefined): string {
  if (n === undefined || !Number.isFinite(n)) return "0"
  return n.toLocaleString()
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
        <div className="space-y-5">
          {/* Provider usage summary — stacks vertically on narrow widths so the
              three stats never squeeze into unreadable 2-digit columns. */}
          <div className="grid gap-2 sm:grid-cols-2">
            {PROVIDERS.map((p) => {
              const totals = usage?.totals_by_provider?.[p]
              return (
                <div
                  key={p}
                  className="min-w-0 rounded-sm border border-border bg-background p-3 text-sm"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate font-semibold">
                      {PROVIDER_LABEL[p]}
                      <span className="ml-1 text-xs font-normal text-muted-foreground">· last 24h</span>
                    </span>
                    {!keysPresent[p] ? (
                      <span className="shrink-0 rounded-sm bg-destructive/10 px-1.5 py-0.5 text-[0.65rem] font-bold tracking-wider text-destructive uppercase">
                        No key
                      </span>
                    ) : null}
                  </div>
                  <dl className="mt-2 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                    <div className="min-w-0">
                      <dt className="truncate">Calls</dt>
                      <dd className="truncate font-mono text-sm text-foreground">{fmtNum(totals?.calls)}</dd>
                    </div>
                    <div className="min-w-0">
                      <dt className="truncate">Tokens</dt>
                      <dd className="truncate font-mono text-sm text-foreground">{fmtNum(totals?.tokens)}</dd>
                    </div>
                    <div className="min-w-0">
                      <dt className="truncate">USD</dt>
                      <dd className="truncate font-mono text-sm text-foreground" title={fmtMoney(totals?.cost_usd)}>
                        {fmtMoney(totals?.cost_usd)}
                      </dd>
                    </div>
                  </dl>
                </div>
              )
            })}
          </div>

          {/* Per-feature matrix. Each row is its own small card so the eye
              groups "this feature → this provider/model" cleanly even when the
              column is 316px wide. */}
          <div className="space-y-2">
            {Object.entries(FEATURE_LABELS).map(([feature, meta]) => {
              const choice = matrix[feature] ?? features.features[feature]
              if (!choice) return null
              const groundedOnly = feature === "shipper_discovery"
              const providerOptions = groundedOnly ? (["gemini"] as const) : PROVIDERS
              const allowed = features.allowed_models[choice.provider as Provider] ?? []
              const noKey = !keysPresent[choice.provider as Provider]
              return (
                <div
                  key={feature}
                  className="min-w-0 rounded-sm border border-border bg-background p-3"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium" title={meta.label}>
                      {meta.label}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{meta.blurb}</p>
                    {noKey ? (
                      <p className="mt-1 text-xs text-destructive">
                        No API key configured for {PROVIDER_LABEL[choice.provider as Provider] ?? choice.provider}.
                      </p>
                    ) : null}
                  </div>

                  {/* Controls: 2-col grid on anything roomy, stacked on the
                      very narrow mobile case. `min-w-0` on each cell lets the
                      triggers (which default to `w-fit`) honor `w-full` and
                      truncate long model ids instead of pushing the row. */}
                  <div className="mt-3 grid grid-cols-1 gap-2 min-[360px]:grid-cols-2">
                    <div className="min-w-0 space-y-1">
                      <label className="text-[0.68rem] font-medium tracking-wide text-muted-foreground uppercase">
                        Provider
                      </label>
                      <Select
                        value={choice.provider}
                        disabled={groundedOnly}
                        onValueChange={(v) => {
                          if (!v) return
                          const nextAllowed = features.allowed_models[v as Provider] ?? []
                          setMatrix((m) => ({
                            ...m,
                            [feature]: { provider: v, model: nextAllowed[0] ?? choice.model },
                          }))
                        }}
                      >
                        <SelectTrigger className="w-full min-w-0">
                          <SelectValue>{PROVIDER_LABEL[choice.provider as Provider] ?? choice.provider}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {providerOptions.map((p) => (
                            <SelectItem key={p} value={p}>
                              {PROVIDER_LABEL[p]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="min-w-0 space-y-1">
                      <label className="text-[0.68rem] font-medium tracking-wide text-muted-foreground uppercase">
                        Model
                      </label>
                      <Select
                        value={choice.model}
                        onValueChange={(v) => {
                          if (!v) return
                          setMatrix((m) => ({ ...m, [feature]: { provider: choice.provider, model: v } }))
                        }}
                      >
                        <SelectTrigger className="w-full min-w-0" title={choice.model}>
                          <SelectValue>
                            <span className="truncate font-mono text-xs">{choice.model}</span>
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {allowed.map((m) => (
                            <SelectItem key={m} value={m}>
                              <span className="font-mono text-xs">{m}</span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
            <Button onClick={save} disabled={saving} className="font-semibold">
              <Save /> {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </Panel>
  )
}
