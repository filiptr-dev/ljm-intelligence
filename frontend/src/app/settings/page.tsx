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
 * The "Unsubscribe link" card (migration 0007) MUST be configured before the
 * auto-outreach toggle can be armed — the server rejects an arm with 409
 * `unsub_config_missing` if the base URL is blank. We disable the toggle
 * client-side too so the shape of the form matches the shape of the truth.
 */

import * as React from "react"
import { toast } from "sonner"
import { HelpCircle, Save } from "lucide-react"
import { PageHeader, Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  DEFAULT_FIT_WEIGHTS,
  FIT_PANEL_INTRO,
  FIT_WEIGHT_META,
} from "./fit-weight-meta"

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
  auto_outreach_min_fit: number
  fit_weights: FitWeights | null
  unsubscribe_base_url: string | null
  unsub_secret_set: boolean
  unsub_config_ready: boolean
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

/** The base URL that the typed openapi-fetch client points at (the FastAPI
 *  origin, not the Next.js origin). The `/unsubscribe` route is served by the
 *  backend — the placeholder guides operators to the correct hostname so we
 *  don't end up minting tokens against `window.location.origin`, which would
 *  404 in production. */
const API_BASE_URL_HINT =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || ""

/** Small info button + tooltip trigger. Accessible via mouse hover, keyboard
 *  focus, and (base-ui default) touch tap. `aria-label` gives screen readers
 *  the weight name so the button doesn't read as "help, help, help …" nine
 *  times in a row. */
function InfoDot({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger
        type="button"
        aria-label={`Help — ${label}`}
        className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none"
      >
        <HelpCircle className="size-3.5" aria-hidden="true" />
      </TooltipTrigger>
      <TooltipContent
        side="top"
        role="tooltip"
        className="max-w-sm whitespace-normal text-left leading-snug"
      >
        {children}
      </TooltipContent>
    </Tooltip>
  )
}

export default function SettingsPage() {
  const [loaded, setLoaded] = React.useState(false)
  const [enabled, setEnabled] = React.useState(false)
  const [threshold, setThreshold] = React.useState(70)
  const [tone, setTone] = React.useState("warm-professional")
  const [dailyCap, setDailyCap] = React.useState(50)
  const [saving, setSaving] = React.useState(false)
  const [updatedAt, setUpdatedAt] = React.useState<string>("")
  const [autoOutreachEnabled, setAutoOutreachEnabled] = React.useState(false)
  const [autoOutreachCap, setAutoOutreachCap] = React.useState(20)
  const [windowStart, setWindowStart] = React.useState(8)
  const [windowEnd, setWindowEnd] = React.useState(18)
  const [statusFilter, setStatusFilter] = React.useState<string>("found")
  const [minFit, setMinFit] = React.useState(60)
  const [fitWeights, setFitWeights] = React.useState<FitWeights>(DEFAULT_FIT_WEIGHTS)
  // Unsubscribe config (migration 0007). The secret VALUE never leaves the API;
  // we only know whether it's set. `unsubReady` gates the auto-outreach toggle.
  const [unsubBaseUrl, setUnsubBaseUrl] = React.useState("")
  const [unsubSecretSet, setUnsubSecretSet] = React.useState(false)
  const [unsubReady, setUnsubReady] = React.useState(false)

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
        setMinFit(s.auto_outreach_min_fit)
        setFitWeights({ ...DEFAULT_FIT_WEIGHTS, ...(s.fit_weights ?? {}) })
        setUnsubBaseUrl(s.unsubscribe_base_url ?? "")
        setUnsubSecretSet(s.unsub_secret_set)
        setUnsubReady(s.unsub_config_ready)
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
          auto_outreach_min_fit: minFit,
          fit_weights: fitWeights,
          unsubscribe_base_url: unsubBaseUrl,
        }),
      })
      if (r.status === 409) {
        // Belt-and-braces: server refuses an arm with a stale-cached ready flag.
        // Roll the local toggle back off and surface the server's message.
        const detail = (await r.json())?.detail
        setAutoOutreachEnabled(false)
        toast.error("Can't arm auto-outreach", {
          description: detail?.message ?? "Unsubscribe config is incomplete.",
        })
        return
      }
      if (!r.ok) throw new Error(`save ${r.status}`)
      const s = (await r.json()) as SettingsOut
      setUpdatedAt(s.updated_at)
      setUnsubBaseUrl(s.unsubscribe_base_url ?? "")
      setUnsubSecretSet(s.unsub_secret_set)
      setUnsubReady(s.unsub_config_ready)
      toast.success("Settings saved")
    } catch (e) {
      toast.error("Couldn't save", { description: String(e) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <TooltipProvider>
      <PageHeader
        eyebrow="Settings"
        title="How the crawler contacts brokers"
        description="You decide when the crawler contacts a broker on its own — this is off by default. Turn it on when you're ready."
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <div className="space-y-5">
          <Panel title="Auto-contact" description="When on, high-fit leads at or above the Minimum fit threshold (set in the Auto-outreach panel below) are contacted automatically after each crawl. Simulated delivery is the demo safety net — nothing leaves the box.">
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
            title="Unsubscribe link"
            description="Every auto-outreach email must carry an unsubscribe link (CAN-SPAM). This is the base URL those links point at; the token appended to it is signed with a secret generated on the server."
          >
            <div className="grid gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="unsub-base-url">Unsubscribe base URL</Label>
                <Input
                  id="unsub-base-url"
                  type="url"
                  pattern="https://.*"
                  placeholder={API_BASE_URL_HINT || "https://api.your-app.example"}
                  value={unsubBaseUrl}
                  onChange={(e) => setUnsubBaseUrl(e.target.value)}
                  disabled={!loaded}
                />
                <p className="text-xs text-muted-foreground">
                  Must be https. This is the <strong>API</strong> origin (the same host your typed API client
                  uses — the value of <code className="font-mono">NEXT_PUBLIC_API_URL</code>), not the frontend
                  origin: <code className="font-mono">/unsubscribe</code> is a backend route.
                </p>
              </div>
              <div
                className={
                  "rounded-sm border p-3 text-sm " +
                  (unsubSecretSet
                    ? "border-emerald-600/50 bg-emerald-500/5 text-emerald-800 dark:text-emerald-300"
                    : "border-amber-600/50 bg-amber-500/5 text-amber-800 dark:text-amber-300")
                }
              >
                Unsubscribe secret: {unsubSecretSet ? "set" : "not set"}
                <span className="ml-2 text-xs text-muted-foreground">
                  (generated on the server; never shown to the browser)
                </span>
              </div>
            </div>
            <div className="mt-5 flex items-center justify-end gap-3 border-t border-border pt-4">
              <Button onClick={save} disabled={!loaded || saving} className="font-semibold">
                <Save /> {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </Panel>

          <Panel
            title="Auto-outreach"
            description="Post-crawl outreach to freshly enriched contacts. Off by default. When on, the daily cron POSTs /enrichment/auto-send with the cron secret; the route still short-circuits if the CAN-SPAM footer or unsubscribe link is not configured."
          >
            <div className="flex items-center justify-between rounded-sm border border-border bg-background p-3">
              <div>
                <div className="font-semibold">Enable auto-outreach</div>
                <div className="text-sm text-muted-foreground">
                  Off by default. Unsubscribes are honored via signed HMAC tokens; the suppression list is checked before every send.
                  {!unsubReady && (
                    <span className="mt-1 block text-amber-700 dark:text-amber-400">
                      Configure the unsubscribe link above before arming auto-outreach.
                    </span>
                  )}
                </div>
              </div>
              <Switch
                checked={autoOutreachEnabled}
                onCheckedChange={setAutoOutreachEnabled}
                disabled={!loaded || !unsubReady}
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
                <div className="flex items-center gap-1.5">
                  <Label htmlFor="min-fit">Minimum fit to auto-contact</Label>
                  <InfoDot label="Minimum fit to auto-contact">
                    <div className="space-y-1.5">
                      <div className="font-semibold">Minimum fit score</div>
                      <div>
                        Auto-outreach only emails contacts whose company scores at or above this
                        threshold (0–100). Unscored leads are always skipped. Within the daily cap,
                        the highest-fit leads go first.
                      </div>
                      <div className="text-muted-foreground">
                        Default 60. Example: at 60, a company at 55 is skipped; raising
                        <code className="mx-1 font-mono">warehouse_or_dc</code> from 15 to 20 lifts it to 60
                        and it becomes eligible.
                      </div>
                    </div>
                  </InfoDot>
                </div>
                <Input
                  id="min-fit"
                  type="number"
                  min={0}
                  max={100}
                  value={minFit}
                  onChange={(e) => setMinFit(Number(e.target.value) || 0)}
                  disabled={!loaded}
                />
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
            description={FIT_PANEL_INTRO.headline}
          >
            <div className="mb-3 space-y-2 rounded-sm border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
              <div>{FIT_PANEL_INTRO.worked_example}</div>
              <div>{FIT_PANEL_INTRO.auto_outreach_link}</div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {Object.keys(DEFAULT_FIT_WEIGHTS).map((k) => {
                const meta = FIT_WEIGHT_META[k as keyof typeof FIT_WEIGHT_META]
                const fallbackLabel = k
                const label = meta?.label ?? fallbackLabel
                return (
                  <div key={k} className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                      <Label htmlFor={`fw-${k}`}>{label}</Label>
                      {meta && (
                        <InfoDot label={label}>
                          <div className="space-y-1.5">
                            <div>
                              <div className="font-semibold">{label}</div>
                              <div className="text-muted-foreground">Default {DEFAULT_FIT_WEIGHTS[k]}</div>
                            </div>
                            <div>
                              <div className="font-semibold">Trigger</div>
                              <div>{meta.trigger}</div>
                            </div>
                            <div>
                              <div className="font-semibold">What the number does</div>
                              <div>{meta.hint}</div>
                            </div>
                            <div>
                              <div className="font-semibold">Example</div>
                              <div>{meta.example}</div>
                            </div>
                            <div>
                              <div className="font-semibold">Effect on auto-contact</div>
                              <div>{meta.auto_outreach_effect}</div>
                            </div>
                          </div>
                        </InfoDot>
                      )}
                    </div>
                    <Input
                      id={`fw-${k}`}
                      type="number"
                      value={fitWeights[k] ?? DEFAULT_FIT_WEIGHTS[k]}
                      onChange={(e) =>
                        setFitWeights((prev) => ({ ...prev, [k]: Number(e.target.value) || 0 }))
                      }
                      disabled={!loaded}
                      aria-describedby={`fw-${k}-code`}
                    />
                    <div id={`fw-${k}-code`} className="font-mono text-[0.68rem] text-muted-foreground">
                      {k}
                    </div>
                  </div>
                )
              })}
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
    </TooltipProvider>
  )
}
