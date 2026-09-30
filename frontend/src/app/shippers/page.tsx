"use client"

/**
 * Shipper Finder — /shippers  (Slice 4)
 *
 * The direct-shipper tool: FMCSA carship='S' + OSM industrial/DC candidates in
 * LJM's 32-state footprint, ranked deterministically by the backend. This page
 * is the first consumer of the new typed `lib/api/` client (see
 * ~/Desktop/bbunikoop-demo/frontend/lib/api/ for the pattern being mirrored) —
 * no /api/* proxy, no ad-hoc fetches. The existing tools keep their proxies
 * untouched; this is the seed of the migration.
 *
 * Layout mirrors /call-list: ranked list on the left, sticky detail column on
 * the right, mobile stacks. Infinite scroll via IntersectionObserver, with a
 * "Load more" fallback after 10 pages so the DOM never runs away (plan gate Q2).
 *
 * "Add to leads" — undo decision (honest by design)
 * -------------------------------------------------
 * Promote is one-way server-side: the plan explicitly rules out un-promote as
 * out of scope. If we POST immediately then "undo" is a lie. So this page
 * *delays* the POST for 6s and shows an undo toast during that window:
 *   - user clicks Undo → we cancel the timer, revert the row's optimistic
 *     "Promoted" state, and no write ever hits the DB.
 *   - timer fires → POST goes; on `created:false` we swap the toast to
 *     "Already in your leads — opened" and keep the row promoted, pointing at
 *     the existing lead id returned by the API.
 *
 * Unmount / navigation while a promote is pending
 * -----------------------------------------------
 * If the user navigates away inside the 6s window we do NOT drop the write.
 * The user's intent was "add to leads" (they didn't click Undo); silently
 * cancelling on unmount would make the UI a liar. So on unmount we clear each
 * scheduled timer and immediately fire its POST (fire-and-forget — the row
 * that would receive the response is already gone). A `sendGuard` per entry
 * makes sure a POST can never fire twice (timer vs unmount flush vs undo).
 *
 * Tab close / hard reload: not covered here. openapi-fetch composes fetch
 * through our client's timeout/retry wrapper, which drops any `keepalive`
 * flag, so a `beforeunload`/`pagehide` flush would need a bespoke raw fetch
 * that bypasses the typed client — out of scope for this fix. Undo pending
 * writes are lost on tab-close.
 */

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"
import { Mail, MapPin, PhoneCall, PlusCircle, ExternalLink } from "lucide-react"

import { EnrichmentPanel } from "@/components/app/enrichment-panel"
import { EnrichmentMetricStrip } from "@/components/app/enrichment-metric-strip"
import { PageHeader, Panel, RegionTag } from "@/components/app/ui"
import { ScoreChip } from "@/components/app/live-feed"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"

import {
  apiConfigured,
} from "@/lib/api/client"
import {
  listShippers,
  getShipper,
  promoteShipper,
  type ShipperRow,
  type ShipperDetail,
  type ShipperSource,
} from "@/lib/api/shipper-finder"

// The 32 in-region states, from backend/app/region.py. Kept short by-hand to
// keep the filter dropdown tidy; the backend enforces the real allowlist.
// Matches backend/app/region.py IN_REGION_STATES exactly (33 entries including DC).
// WA is NOT in the footprint; DC and OK are. Keep in sync with region.py.
const IN_REGION_STATES = [
  "AL","AR","CT","DC","DE","FL","GA","IA","IL","IN","KY","LA","MA","MD","ME","MI",
  "MN","MO","MS","NC","NH","NJ","NY","OH","OK","PA","RI","SC","TN","VA","VT","WI",
  "WV",
]

const PAGE_LIMIT = 50
const LOAD_MORE_AFTER_PAGES = 10
const UNDO_MS = 6000

type FiltersState = {
  state: string // "" = any
  source: ShipperSource | ""
  min_score: number
  promoted: boolean
  q: string
}

const EMPTY_FILTERS: FiltersState = {
  state: "",
  source: "",
  min_score: 0,
  promoted: false,
  q: "",
}

export default function ShippersPage() {
  const [filters, setFilters] = React.useState<FiltersState>(EMPTY_FILTERS)
  const [rows, setRows] = React.useState<ShipperRow[]>([])
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [pageCount, setPageCount] = React.useState(0)
  const [loading, setLoading] = React.useState(false)
  const [initialLoad, setInitialLoad] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const [detail, setDetail] = React.useState<ShipperDetail | null>(null)
  const [detailLoading, setDetailLoading] = React.useState(false)

  // Pending promotes: candidate_id -> entry. Used by the undo path AND by the
  // unmount flush. `send` is the shared committer (used by the timer, by the
  // unmount flush, and never by Undo); a per-entry `sent` guard makes the
  // committer idempotent so timer-vs-flush races cannot double-POST. Undo
  // clears the timer, restores state, and deletes the entry BEFORE `send`
  // ever runs, so Undo continues to cancel cleanly.
  type PendingEntry = {
    timer: ReturnType<typeof setTimeout>
    restore: () => void
    send: () => void
    sent: boolean
  }
  const pendingRef = React.useRef<Map<string, PendingEntry>>(new Map())
  // Track mount state so we can skip setState calls after unmount (React would
  // warn / no-op anyway, but explicit is kinder to future readers).
  const mountedRef = React.useRef(true)

  const loadingRef = React.useRef(false)
  const requestSeqRef = React.useRef(0)

  const fetchPage = React.useCallback(
    async (opts: { reset: boolean; cursor?: string | null; filters: FiltersState }) => {
      if (loadingRef.current) return
      if (!apiConfigured) {
        setError(
          "NEXT_PUBLIC_API_URL is not set — add it to frontend/.env.local (see README).",
        )
        setInitialLoad(false)
        return
      }
      loadingRef.current = true
      setLoading(true)
      setError(null)
      const seq = ++requestSeqRef.current
      try {
        const query = {
          state: opts.filters.state || undefined,
          source: opts.filters.source || undefined,
          min_score: opts.filters.min_score > 0 ? opts.filters.min_score : undefined,
          promoted: opts.filters.promoted ? true : undefined,
          q: opts.filters.q.trim() || undefined,
          cursor: opts.cursor ?? undefined,
          limit: PAGE_LIMIT,
        }
        const data = await listShippers(query)
        // A stale response from a filter that has since changed must not overwrite the current view.
        if (seq !== requestSeqRef.current) return
        setRows((prev) => (opts.reset ? data.items : [...prev, ...data.items]))
        setCursor(data.next_cursor ?? null)
        setPageCount((n) => (opts.reset ? 1 : n + 1))
        if (opts.reset) {
          setSelectedId(data.items[0]?.id ?? null)
        }
      } catch (e) {
        if (seq !== requestSeqRef.current) return
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (seq === requestSeqRef.current) {
          setLoading(false)
          setInitialLoad(false)
        }
        loadingRef.current = false
      }
    },
    [],
  )

  // Refetch from scratch whenever filters change (debounced for `q`).
  React.useEffect(() => {
    const t = setTimeout(() => {
      void fetchPage({ reset: true, cursor: null, filters })
    }, 200)
    return () => clearTimeout(t)
  }, [filters, fetchPage])

  // Right-column detail: fetch when selection changes.
  React.useEffect(() => {
    if (!selectedId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    ;(async () => {
      try {
        const d = await getShipper(selectedId)
        if (!cancelled) setDetail(d)
      } catch (e) {
        if (!cancelled) {
          setDetail(null)
          toast.error("Couldn't load shipper detail", { description: String(e) })
        }
      } finally {
        if (!cancelled) setDetailLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedId])

  // IntersectionObserver on the "next-page" sentinel — auto-fetch until the
  // Load-More cap kicks in, then stop and let the operator click.
  const sentinelRef = React.useRef<HTMLDivElement | null>(null)
  React.useEffect(() => {
    const el = sentinelRef.current
    if (!el || !cursor) return
    if (pageCount >= LOAD_MORE_AFTER_PAGES) return
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !loadingRef.current) {
        void fetchPage({ reset: false, cursor, filters })
      }
    })
    io.observe(el)
    return () => io.disconnect()
  }, [cursor, pageCount, filters, fetchPage])

  const handlePromote = (row: ShipperRow) => {
    if (row.promoted_lead_id) return
    // Optimistic flip. Rollback is the exact previous row state.
    const previous = row
    setRows((current) =>
      current.map((r) =>
        r.id === row.id
          ? { ...r, promoted_lead_id: "pending", reasons: [...r.reasons, "Added to leads"] }
          : r,
      ),
    )
    const restore = () => {
      if (!mountedRef.current) return
      setRows((current) => current.map((r) => (r.id === row.id ? previous : r)))
    }
    // One shared committer, whether the 6s timer fires OR unmount flushes early.
    // The `sent` guard makes it safe to call twice (the timer may already be
    // in-flight when unmount tries to flush).
    const send = () => {
      const entry = pendingRef.current.get(row.id)
      if (entry) {
        if (entry.sent) return
        entry.sent = true
        clearTimeout(entry.timer)
        pendingRef.current.delete(row.id)
      }
      void (async () => {
        try {
          const res = await promoteShipper(row.id)
          if (!mountedRef.current) return
          setRows((current) =>
            current.map((r) => (r.id === row.id ? { ...r, promoted_lead_id: res.lead_id } : r)),
          )
          if (!res.created) {
            toast("Already in your leads — opened", {
              description: `${row.name} is already tracked; row linked to that lead.`,
            })
          }
        } catch (e) {
          if (!mountedRef.current) return
          restore()
          toast.error("Couldn't add to leads", { description: String(e) })
        }
      })()
    }
    const timer = setTimeout(send, UNDO_MS)
    pendingRef.current.set(row.id, { timer, restore, send, sent: false })
    toast("Adding to leads…", {
      description: `${row.name} — you have 6s to undo.`,
      duration: UNDO_MS,
      action: {
        label: "Undo",
        onClick: () => {
          const p = pendingRef.current.get(row.id)
          if (p) {
            clearTimeout(p.timer)
            p.restore()
            pendingRef.current.delete(row.id)
          }
        },
      },
    })
  }

  // On unmount: flush any in-flight promote timers immediately. The user's
  // intent was "add to leads" — if they leave inside the 6s window without
  // clicking Undo, we should honour the intent, not silently drop it.
  // `send()` clears the timer, marks the entry sent, removes it from the map,
  // and fires the POST fire-and-forget.
  React.useEffect(() => {
    const pending = pendingRef.current
    return () => {
      mountedRef.current = false
      for (const entry of Array.from(pending.values())) entry.send()
    }
  }, [])

  const selected = React.useMemo(
    () => rows.find((r) => r.id === selectedId) ?? null,
    [rows, selectedId],
  )
  const showLoadMoreButton = pageCount >= LOAD_MORE_AFTER_PAGES && cursor !== null

  return (
    <>
      <PageHeader
        eyebrow="Shipper Finder"
        title="Direct shippers in your footprint"
        description="Manufacturers, warehouses and DCs in LJM's 32-state region — FMCSA carship='S' plus OpenStreetMap Overpass, ranked by lane fit. Deterministic; one click adds a row to the leads pipeline."
        actions={
          <span className="text-xs text-muted-foreground">
            {loading && rows.length === 0 ? "Loading…" : `${rows.length} shown`}
          </span>
        }
      />

      <EnrichmentMetricStrip scope="shipper" />
      <FilterBar filters={filters} onChange={setFilters} />

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,460px)]">
        {/* --- ranked list --- */}
        <div className="space-y-5">
          <Panel
            title="Ranked shippers"
            description={
              initialLoad
                ? "Ranking…"
                : error
                  ? "Backend unreachable — see the message below."
                  : rows.length
                    ? `${rows.length} shown · pick a row to see evidence and add it to your leads`
                    : "No shippers match — try loosening state / source / min-score, or run the crawler."
            }
          >
            {error ? (
              <p className="text-sm text-warn">{error}</p>
            ) : initialLoad ? (
              <p className="text-sm text-muted-foreground">Ranking…</p>
            ) : rows.length === 0 ? (
              <EmptyState />
            ) : (
              <>
                <ul className="space-y-2">
                  {rows.map((row, i) => (
                    <ShipperRowItem
                      key={row.id}
                      row={row}
                      index={i}
                      selected={row.id === selectedId}
                      onSelect={() => setSelectedId(row.id)}
                      onPromote={() => handlePromote(row)}
                    />
                  ))}
                </ul>

                {cursor ? (
                  <div ref={sentinelRef} className="mt-3 flex items-center justify-center">
                    {showLoadMoreButton ? (
                      <Button
                        variant="outline"
                        onClick={() => void fetchPage({ reset: false, cursor, filters })}
                        disabled={loading}
                      >
                        {loading ? "Loading…" : "Load more"}
                      </Button>
                    ) : loading ? (
                      <span className="text-xs text-muted-foreground">Loading more…</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">Scroll for more</span>
                    )}
                  </div>
                ) : rows.length > 0 ? (
                  <div className="mt-3 text-center text-xs text-muted-foreground">
                    End of list — {rows.length} shippers ranked.
                  </div>
                ) : null}
              </>
            )}
          </Panel>
        </div>

        {/* --- detail column --- */}
        <div className="space-y-4 xl:sticky xl:top-20 xl:self-start">
          <Panel
            title="Evidence"
            description={selected ? `Details for ${selected.name}` : "Pick a row to see evidence and reach out."}
          >
            {!selected ? (
              <p className="text-sm text-muted-foreground">
                Pick a shipper from the list. Its FMCSA + OSM evidence, address
                and map link appear here. Once the shipper is in your leads,
                you&apos;ll also get an Email button.
              </p>
            ) : (
              <DetailBody row={selected} detail={detail} detailLoading={detailLoading} />
            )}
          </Panel>
        </div>
      </div>
    </>
  )
}

// ---- filter bar -------------------------------------------------------------

function FilterBar({
  filters,
  onChange,
}: {
  filters: FiltersState
  onChange: (f: FiltersState) => void
}) {
  return (
    <div className="grid gap-3 rounded-sm border border-border bg-card p-3 md:grid-cols-[minmax(0,1fr)_140px_140px_120px_auto]">
      <div>
        <Label htmlFor="q" className="text-[0.7rem] text-muted-foreground">
          Search
        </Label>
        <Input
          id="q"
          placeholder="Company name…"
          value={filters.q}
          onChange={(e) => onChange({ ...filters, q: e.target.value })}
        />
      </div>
      <div>
        <Label className="text-[0.7rem] text-muted-foreground">State</Label>
        <Select
          value={filters.state || "ALL"}
          onValueChange={(v) => v && onChange({ ...filters, state: v === "ALL" ? "" : v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Any" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">Any state</SelectItem>
            {IN_REGION_STATES.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div>
        <Label className="text-[0.7rem] text-muted-foreground">Source</Label>
        <Select
          value={filters.source || "ANY"}
          onValueChange={(v) =>
            v && onChange({ ...filters, source: v === "ANY" ? "" : (v as ShipperSource) })
          }
        >
          <SelectTrigger>
            <SelectValue placeholder="Any" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ANY">Any</SelectItem>
            <SelectItem value="FMCSA">FMCSA</SelectItem>
            <SelectItem value="OSM">OSM</SelectItem>
            <SelectItem value="Both">Both (cross-confirmed)</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div>
        <Label htmlFor="min-score" className="text-[0.7rem] text-muted-foreground">
          Min score {filters.min_score > 0 ? `(${filters.min_score})` : ""}
        </Label>
        <Input
          id="min-score"
          type="number"
          min={0}
          max={100}
          value={filters.min_score}
          onChange={(e) =>
            onChange({
              ...filters,
              min_score: Number.isFinite(Number(e.target.value))
                ? Math.max(0, Math.min(100, Number(e.target.value)))
                : 0,
            })
          }
        />
      </div>
      <div className="flex items-end gap-2 pb-1">
        <div className="flex items-center gap-2">
          <Switch
            id="promoted"
            checked={filters.promoted}
            onCheckedChange={(v) => onChange({ ...filters, promoted: !!v })}
          />
          <Label htmlFor="promoted" className="text-xs">
            Only promoted
          </Label>
        </div>
      </div>
    </div>
  )
}

// ---- one row ----------------------------------------------------------------

function ShipperRowItem({
  row,
  index,
  selected,
  onSelect,
  onPromote,
}: {
  row: ShipperRow
  index: number
  selected: boolean
  onSelect: () => void
  onPromote: () => void
}) {
  const promoted = Boolean(row.promoted_lead_id)
  const promotedRealId =
    promoted && row.promoted_lead_id !== "pending" ? row.promoted_lead_id : null

  return (
    <li>
      <div
        className={cn(
          "flex flex-col gap-2 rounded-sm border px-3 py-2.5 transition sm:flex-row sm:items-start",
          selected ? "border-asphalt bg-accent" : "border-border hover:bg-muted",
        )}
      >
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-start gap-3 text-left"
        >
          <span className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-[3px] bg-asphalt font-mono text-[0.7rem] font-bold text-white">
            {index + 1}
          </span>
          <RegionTag region="US" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate font-semibold">{row.name}</span>
              <span className="text-xs text-muted-foreground">
                {row.city ? `${row.city}, ` : ""}
                {row.state}
              </span>
              <span className="ml-auto">
                <ScoreChip score={row.score} />
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {row.sources.map((s) => (
                <SourceBadge key={s} source={s} />
              ))}
              {row.reasons.map((reason) => (
                <ReasonChip key={reason} label={reason} />
              ))}
              {typeof row.fit_score === "number" ? (
                <span
                  className="inline-flex items-center gap-1 rounded-sm bg-chart-1/10 px-2 py-0.5 text-xs font-semibold text-chart-1"
                  title={(row.fit_reasons ?? []).join(" · ")}
                >
                  Fit {row.fit_score}
                </span>
              ) : null}
            </div>
          </div>
        </button>

        <div className="flex flex-wrap items-center gap-2 sm:flex-col sm:items-stretch sm:gap-1.5">
          {row.phone ? (
            <a
              href={`tel:${row.phone}`}
              className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-safety px-3 text-sm font-semibold text-asphalt hover:opacity-90"
            >
              <PhoneCall className="size-4" />
              <span className="font-mono">{row.phone}</span>
            </a>
          ) : (
            <span className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-dashed border-border px-3 text-xs text-muted-foreground">
              No phone
            </span>
          )}

          {promoted ? (
            <Link
              href={promotedRealId ? `/call-list?lead=${encodeURIComponent(promotedRealId)}` : "/call-list"}
              className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-muted"
            >
              <ExternalLink className="size-4" />
              Open in call list
            </Link>
          ) : (
            <Button
              type="button"
              onClick={onPromote}
              className="min-h-9 justify-start"
            >
              <PlusCircle className="size-4" /> Add to leads
            </Button>
          )}
        </div>
      </div>
    </li>
  )
}

function SourceBadge({ source }: { source: string }) {
  const tone =
    source === "FMCSA"
      ? "bg-good/15 text-good border-good/40"
      : "bg-chart-2/15 text-chart-2 border-chart-2/40"
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-[3px] border px-1.5 py-0.5 text-[0.65rem] font-semibold tracking-wider uppercase",
        tone,
      )}
    >
      {source}
    </span>
  )
}

function ReasonChip({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-[0.68rem] font-medium text-foreground">
      {label}
    </span>
  )
}

// ---- detail column ----------------------------------------------------------

function DetailBody({
  row,
  detail,
  detailLoading,
}: {
  row: ShipperRow
  detail: ShipperDetail | null
  detailLoading: boolean
}) {
  const promoted = Boolean(row.promoted_lead_id) && row.promoted_lead_id !== "pending"
  const mapHref =
    row.lat != null && row.lng != null
      ? `https://www.google.com/maps/search/?api=1&query=${row.lat},${row.lng}`
      : null
  const evidenceMatch =
    detail?.evidence && typeof detail.evidence === "object"
      ? ((detail.evidence as Record<string, unknown>)["match"] as Record<string, unknown> | undefined)
      : undefined
  const fmcsaEvidence =
    detail?.evidence && typeof detail.evidence === "object"
      ? ((detail.evidence as Record<string, unknown>)["fmcsa"] as Record<string, unknown> | undefined)
      : undefined
  const osmEvidence =
    detail?.evidence && typeof detail.evidence === "object"
      ? ((detail.evidence as Record<string, unknown>)["osm"] as Record<string, unknown> | undefined)
      : undefined
  const isCrossConfirmed = row.sources.length > 1

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold">{row.name}</h3>
          <ScoreChip score={row.score} />
        </div>
        <p className="text-sm text-muted-foreground">
          {row.address ? `${row.address} · ` : row.city ? `${row.city}, ` : ""}
          {row.state}
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {row.sources.map((s) => (
          <SourceBadge key={s} source={s} />
        ))}
        {row.reasons.map((reason) => (
          <ReasonChip key={reason} label={reason} />
        ))}
      </div>

      <EnrichmentPanel candidateId={row.id} leadId={row.promoted_lead_id ?? null} />
      {typeof row.fit_score === "number" ? (
        <div className="rounded-sm border border-border bg-muted/40 p-2 text-xs">
          <div className="font-semibold">Fit {row.fit_score} / 100</div>
          <ul className="mt-1 list-disc pl-5">
            {(row.fit_reasons ?? []).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2 text-xs">
        <Field label="Phone" value={row.phone ? <a href={`tel:${row.phone}`} className="font-mono underline">{row.phone}</a> : "—"} />
        <Field label="Email" value={row.primary_email ?? "—"} />
        <Field label="MC" value={row.mc ?? detail?.fmcsa_mc ?? "—"} />
        <Field label="DOT" value={row.dot ?? detail?.fmcsa_dot ?? "—"} />
        <Field label="Domain" value={row.domain ?? "—"} />
        <Field
          label="OSM ref"
          value={detail?.osm_ref ? (
            <a
              href={`https://www.openstreetmap.org/${detail.osm_ref}`}
              target="_blank"
              rel="noreferrer"
              className="font-mono underline"
            >
              {detail.osm_ref}
            </a>
          ) : "—"}
        />
      </div>

      {mapHref ? (
        <a
          href={mapHref}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-muted"
        >
          <MapPin className="size-4" /> Map ({row.lat!.toFixed(4)}, {row.lng!.toFixed(4)})
        </a>
      ) : null}

      {promoted ? (
        <Link
          href={`/emails/compose?lead=${encodeURIComponent(row.promoted_lead_id!)}`}
          className="inline-flex h-10 items-center justify-center gap-1.5 rounded-sm bg-safety px-3 text-sm font-semibold text-asphalt hover:opacity-90"
        >
          <Mail className="size-4" /> Email
        </Link>
      ) : (
        <p className="text-[0.7rem] text-muted-foreground">
          Add this shipper to leads to unlock the email composer.
        </p>
      )}

      {isCrossConfirmed && (row.match_reason || evidenceMatch) ? (
        <div className="rounded-sm border border-border bg-background p-2.5">
          <div className="text-[0.7rem] font-semibold tracking-wider text-muted-foreground uppercase">
            Why merged
          </div>
          {row.match_reason ? (
            <p className="mt-1 text-sm">{row.match_reason}</p>
          ) : null}
          {evidenceMatch ? (
            <pre className="mt-1 max-h-40 overflow-auto text-[0.7rem] text-muted-foreground">
              {JSON.stringify(evidenceMatch, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}

      {detailLoading ? (
        <p className="text-xs text-muted-foreground">Loading evidence…</p>
      ) : detail ? (
        <div className="space-y-2">
          {fmcsaEvidence ? (
            <EvidenceBlock title="FMCSA evidence" data={fmcsaEvidence} />
          ) : null}
          {osmEvidence ? <EvidenceBlock title="OSM evidence" data={osmEvidence} /> : null}
          {detail.osm_tags && Object.keys(detail.osm_tags).length > 0 ? (
            <EvidenceBlock title="OSM tags" data={detail.osm_tags as Record<string, unknown>} />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-border bg-background px-2 py-1.5">
      <div className="text-[0.6rem] font-semibold tracking-wider text-muted-foreground uppercase">
        {label}
      </div>
      <div className="truncate text-sm">{value}</div>
    </div>
  )
}

function EvidenceBlock({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <details className="rounded-sm border border-border bg-background p-2">
      <summary className="cursor-pointer text-[0.7rem] font-semibold tracking-wider text-muted-foreground uppercase">
        {title}
      </summary>
      <pre className="mt-2 max-h-56 overflow-auto text-[0.7rem]">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  )
}

function EmptyState() {
  return (
    <p className="text-sm text-muted-foreground">
      No shippers match — try loosening state / source / min-score, or run the
      crawler from the{" "}
      <Link href="/" className="underline">
        engine
      </Link>{" "}
      to bring fresh candidates in.
    </p>
  )
}
