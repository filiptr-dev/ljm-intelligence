"use client"

/**
 * Owner settings — /settings
 *
 * Persists to the backend `settings` singleton via /api/settings.
 * Fields the owner cares about:
 *  - Auto-send toggle (default OFF)
 *  - Match threshold (default 70)
 *  - Tone (used by the "enhance with AI" pass when it runs)
 *  - Daily cap (CAN-SPAM guard, default 50)
 *
 * Template picker is stubbed until the shared email builder's saved templates
 * land — the "Coming next" line is honest about that.
 */

import * as React from "react"
import { toast } from "sonner"
import { Save } from "lucide-react"
import { PageHeader, Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

type FitWeights = Record<string, number>

type SettingsOut = {
  threshold: number
  auto_send_enabled: boolean
  auto_send_template_id: string | null
  tone: string
  daily_send_cap: number
  updated_at: string
  auto_outreach_enabled: boolean
  auto_outreach_template_id: string | null
  auto_outreach_daily_cap: number
  auto_outreach_window_start_h: number
  auto_outreach_window_end_h: number
  auto_outreach_status_filter: string
  fit_weights: FitWeights | null
}

// Mirrors backend DEFAULT_WEIGHTS in app/scoring/fit_score.py. Any override
// missing a key falls back to this — the UI just seeds the editor here.
const DEFAULT_FIT_WEIGHTS: FitWeights = {
  in_region: 15,
  named_dm_direct_contact: 25,
  warehouse_or_dc: 15,
  ships_nationwide: 10,
  industry_freight_heavy: 10,
  equipment_hint: 10,
  locations_bonus_per_extra: 3,
  has_phone: 5,
  penalty_generic_email_only: -10,
}

const STATUS_FILTERS = [
  { value: "found", label: "Found (never contacted)" },
  { value: "contacted", label: "Contacted (retry)" },
  { value: "any", label: "Any status" },
]

const TONES = [
  { value: "warm-professional", label: "Warm / Professional" },
  { value: "brief-direct", label: "Brief / Direct" },
  { value: "friendly", label: "Friendly" },
]

export default function SettingsPage() {
  const [loaded, setLoaded] = React.useState(false)
  const [enabled, setEnabled] = React.useState(false)
  const [threshold, setThreshold] = React.useState(70)
  const [tone, setTone] = React.useState("warm-professional")
  const [dailyCap, setDailyCap] = React.useState(50)
  const [saving, setSaving] = React.useState(false)
  const [updatedAt, setUpdatedAt] = React.useState<string>("")
  // Auto-outreach (scope change 2026-09-30). OFF by default; cron POSTs
  // /enrichment/auto-send after every crawl, but the route no-ops unless this
  // toggle is on and the CAN-SPAM footer address is configured server-side.
  const [autoOutreachEnabled, setAutoOutreachEnabled] = React.useState(false)
  const [autoOutreachCap, setAutoOutreachCap] = React.useState(20)
  const [windowStart, setWindowStart] = React.useState(8)
  const [windowEnd, setWindowEnd] = React.useState(18)
  const [statusFilter, setStatusFilter] = React.useState<string>("found")
  const [fitWeights, setFitWeights] = React.useState<FitWeights>(DEFAULT_FIT_WEIGHTS)

  React.useEffect(() => {
    ;(async () => {
      try {
        const r = await fetch("/api/settings", { cache: "no-store" })
        if (!r.ok) throw new Error(`settings ${r.status}`)
        const s = (await r.json()) as SettingsOut
        setEnabled(s.auto_send_enabled)
        setThreshold(s.threshold)
        setTone(s.tone)
        setDailyCap(s.daily_send_cap)
        setUpdatedAt(s.updated_at)
        setAutoOutreachEnabled(s.auto_outreach_enabled)
        setAutoOutreachCap(s.auto_outreach_daily_cap)
        setWindowStart(s.auto_outreach_window_start_h)
        setWindowEnd(s.auto_outreach_window_end_h)
        setStatusFilter(s.auto_outreach_status_filter)
        // Merge server overrides over defaults so missing keys stay visible.
        setFitWeights({ ...DEFAULT_FIT_WEIGHTS, ...(s.fit_weights ?? {}) })
      } catch (e) {
        toast.error("Couldn't load settings", { description: String(e) })
      } finally {
        setLoaded(true)
      }
    })()
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      const r = await fetch("/api/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          auto_send_enabled: enabled,
          threshold,
          tone,
          daily_send_cap: dailyCap,
          auto_outreach_enabled: autoOutreachEnabled,
          auto_outreach_daily_cap: autoOutreachCap,
          auto_outreach_window_start_h: windowStart,
          auto_outreach_window_end_h: windowEnd,
          auto_outreach_status_filter: statusFilter,
          fit_weights: fitWeights,
        }),
      })
      if (!r.ok) throw new Error(`save ${r.status}`)
      const s = (await r.json()) as SettingsOut
      setUpdatedAt(s.updated_at)
      toast.success("Settings saved")
    } catch (e) {
      toast.error("Couldn't save", { description: String(e) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="How the crawler contacts brokers"
        description="You decide when the crawler contacts a broker on its own — this is off by default. Turn it on when you're ready."
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <div className="space-y-5">
          <Panel title="Auto-contact" description="When on, high-fit leads are contacted automatically after each crawl. Simulated delivery is the demo safety net — nothing leaves the box.">
            <div className="flex items-center justify-between rounded-sm border border-border bg-background p-3">
              <div>
                <div className="font-semibold">Send emails automatically</div>
                <div className="text-sm text-muted-foreground">Default off. Turn on when you want the crawler to reach out for you.</div>
              </div>
              <Switch checked={enabled} onCheckedChange={setEnabled} disabled={!loaded} aria-label="Auto-send toggle" />
            </div>

            <div className="mt-4 space-y-3">
              <div>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <Label>Match score threshold</Label>
                  <span className="font-mono text-sm">{threshold}</span>
                </div>
                <Slider
                  value={[threshold]}
                  onValueChange={(v) => {
                    const arr = Array.isArray(v) ? v : [v]
                    setThreshold(arr[0] ?? 70)
                  }}
                  min={0}
                  max={100}
                  step={5}
                  disabled={!loaded}
                />
                <p className="mt-1 text-xs text-muted-foreground">Only leads scoring at or above this value get auto-contacted.</p>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Tone</Label>
                  <Select value={tone} onValueChange={(v) => v && setTone(v)}>
                    <SelectTrigger><SelectValue>{TONES.find((t) => t.value === tone)?.label ?? tone}</SelectValue></SelectTrigger>
                    <SelectContent>{TONES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>Daily send cap</Label>
                  <Input type="number" min={1} max={1000} value={dailyCap} onChange={(e) => setDailyCap(Number(e.target.value) || 0)} />
                  <p className="text-xs text-muted-foreground">CAN-SPAM guard. Default 50.</p>
                </div>
              </div>
            </div>

            <div className="mt-5 flex items-center justify-end gap-3 border-t border-border pt-4">
              <span className="text-xs text-muted-foreground" suppressHydrationWarning>{updatedAt ? `Last saved ${new Date(updatedAt).toLocaleString()}` : ""}</span>
              <Button onClick={save} disabled={!loaded || saving} className="font-semibold">
                <Save /> {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </Panel>

          <Panel
            title="Auto-outreach"
            description="Post-crawl outreach to freshly enriched contacts. Off by default. When on, the daily cron POSTs /enrichment/auto-send with the cron secret; the route still short-circuits if the CAN-SPAM footer is not configured."
          >
            <div className="flex items-center justify-between rounded-sm border border-border bg-background p-3">
              <div>
                <div className="font-semibold">Enable auto-outreach</div>
                <div className="text-sm text-muted-foreground">Off by default. Unsubscribes are honored via signed HMAC tokens; the suppression list is checked before every send.</div>
              </div>
              <Switch
                checked={autoOutreachEnabled}
                onCheckedChange={setAutoOutreachEnabled}
                disabled={!loaded}
                aria-label="Auto-outreach toggle"
              />
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Daily cap</Label>
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={autoOutreachCap}
                  onChange={(e) => setAutoOutreachCap(Number(e.target.value) || 0)}
                  disabled={!loaded}
                />
                <p className="text-xs text-muted-foreground">Hard cap per 24h. Skipped-cap counter surfaces in the run report.</p>
              </div>
              <div className="space-y-1.5">
                <Label>Status filter</Label>
                <Select value={statusFilter} onValueChange={(v) => v && setStatusFilter(v)}>
                  <SelectTrigger><SelectValue>{STATUS_FILTERS.find((s) => s.value === statusFilter)?.label ?? statusFilter}</SelectValue></SelectTrigger>
                  <SelectContent>{STATUS_FILTERS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}</SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">Which contacts qualify. Default: never contacted before.</p>
              </div>
              <div className="space-y-1.5">
                <Label>Send window — start (local hour)</Label>
                <Input
                  type="number"
                  min={0}
                  max={23}
                  value={windowStart}
                  onChange={(e) => setWindowStart(Number(e.target.value) || 0)}
                  disabled={!loaded}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Send window — end (local hour)</Label>
                <Input
                  type="number"
                  min={0}
                  max={23}
                  value={windowEnd}
                  onChange={(e) => setWindowEnd(Number(e.target.value) || 0)}
                  disabled={!loaded}
                />
                <p className="text-xs text-muted-foreground">Equal start/end → always in window.</p>
              </div>
            </div>

            <div className="mt-5 flex items-center justify-end gap-3 border-t border-border pt-4">
              <Button onClick={save} disabled={!loaded || saving} className="font-semibold">
                <Save /> {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </Panel>

          <Panel
            title="Fit-score weights"
            description="Ops overrides for the deterministic 0..100 fit score. Missing keys fall back to the code defaults; negative values are penalties. Widen or narrow a signal without a deploy."
          >
            <div className="grid gap-3 sm:grid-cols-2">
              {Object.keys(DEFAULT_FIT_WEIGHTS).map((k) => (
                <div key={k} className="space-y-1.5">
                  <Label className="font-mono text-xs">{k}</Label>
                  <Input
                    type="number"
                    value={fitWeights[k] ?? DEFAULT_FIT_WEIGHTS[k]}
                    onChange={(e) =>
                      setFitWeights((prev) => ({ ...prev, [k]: Number(e.target.value) || 0 }))
                    }
                    disabled={!loaded}
                  />
                </div>
              ))}
            </div>
            <div className="mt-5 flex items-center justify-between gap-3 border-t border-border pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => setFitWeights(DEFAULT_FIT_WEIGHTS)}
                disabled={!loaded}
                className="font-semibold"
              >
                Reset to defaults
              </Button>
              <Button onClick={save} disabled={!loaded || saving} className="font-semibold">
                <Save /> {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </Panel>

          <Panel title="Template" description="Which saved template auto-send uses. Pickable from the email builder's saved templates.">
            <p className="text-sm text-muted-foreground">
              <span className="mr-2 inline-flex items-center rounded-sm bg-safety px-2 py-0.5 text-[0.68rem] font-bold tracking-wider text-asphalt uppercase">Coming next</span>
              The template picker unlocks once the shared email builder&apos;s saved templates ship.
            </p>
          </Panel>
        </div>

        <div className="space-y-4 xl:sticky xl:top-20 xl:self-start">
          <Panel title="How this works">
            <ol className="list-decimal space-y-2 pl-5 text-sm text-muted-foreground marker:text-safety">
              <li>The crawler finds new brokers/shippers every day at 3 PM ET.</li>
              <li>Each lead gets an AI match score against LJM.</li>
              <li>If auto-send is on, leads scoring ≥ threshold get emailed automatically.</li>
              <li>All sends are logged and capped by the daily cap.</li>
              <li>Unsubscribed addresses are always skipped.</li>
            </ol>
          </Panel>
        </div>
      </div>
    </>
  )
}
