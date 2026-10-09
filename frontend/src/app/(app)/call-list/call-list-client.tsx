"use client"

/**
 * Call List — /call-list
 *
 * The operator's daily phone queue. ≤25 rows ranked server-side by the
 * deterministic `call_rank` scorer (backend/app/pipeline/call_rank.py). This
 * page is a pure client: it fetches through the Next proxy at /api/call-list,
 * lets the operator log an outcome, then replaces the list with whatever the
 * backend returned.
 *
 * Layout follows /capacity: ranked list on the left, a sticky detail column on
 * the right (desktop). On mobile the detail stacks under the list — the row's
 * phone is a tel: tap-to-call so calling still works with one hand.
 *
 * Wiring: gate decision (a) — same Next `/api/*` proxy + `src/lib/backend.ts`
 * style Capacity uses. A typed `lib/api/` client migration is a separate task.
 *
 * Honesty: `useBackendHealth()` flips a LIVE/SIMULATED badge; when the backend
 * is down we say so instead of pretending — there is no seeded fallback list,
 * because a fake call list would be worse than an empty one.
 */

import * as React from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { toast } from "sonner"
import {
  Phone,
  Mail,
  PhoneCall,
  CalendarClock,
  CheckCircle2,
  XCircle,
  PhoneOff,
  Sparkles,
} from "lucide-react"
import { PageHeader, Panel, RegionTag } from "@/components/app/ui"
import { ScoreChip } from "@/components/app/live-feed"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { useBackendHealth } from "@/lib/use-backend"
import { api } from "@/lib/api/client"
import { cn } from "@/lib/utils"

// ---- shapes -----------------------------------------------------------------
// Kept in sync by hand with backend/app/api/call_list.py::CallRowOut. When the
// typed openapi-fetch client lands (separate task) these move to lib/api/.

type LastOutcome = { outcome: string; logged_at: string | null }

export type CallRow = {
  lead_id: string
  name: string
  state: string
  city: string | null
  phone: string
  primary_email: string | null
  score: number
  reasons: string[]
  opener: string
  last_outcome: LastOutcome | null
}

export type CallListEnvelope = {
  date: string
  items: CallRow[]
  // Only present on POST /outcome responses — the id of the row we just
  // inserted. The toast's Undo action calls DELETE /outcome/{id}.
  logged_outcome_id?: number | null
}

type OutcomeKind = "booked" | "callback" | "not_interested" | "no_answer"

type HistoryRow = {
  id: number
  outcome: string
  callback_at: string | null
  note: string | null
  logged_at: string
  logged_by: string | null
}

// ---- helpers ----------------------------------------------------------------

/** +N days as ISO date, using local calendar date (the picker sends local date strings).
 *  NB: toISOString() is UTC — at 9pm ET that rolls to the next calendar day and
 *  shifts +2 days to +3 days, and makes "today" tomorrow. Use local parts instead.
 */
function plusDaysISO(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

const OUTCOME_LABEL: Record<string, string> = {
  booked: "Booked",
  callback: "Call back",
  not_interested: "Not interested",
  no_answer: "No answer",
}

// ---- page -------------------------------------------------------------------

export default function CallListClient({ initial }: { initial: CallListEnvelope | null }) {
  const { live } = useBackendHealth()
  // Follow-ups "Log call" deep-links here with ?lead=<id>. If the id is in
  // today's ranked list we land on it; otherwise the normal first-row default
  // takes over. Reads once at mount — a navigation back to /call-list without
  // the param keeps the user's current selection.
  const searchParams = useSearchParams()
  const requestedLead = searchParams?.get("lead") ?? null

  // SSR seed: the Server Component resolved the first `GET /tools/call-list`
  // with the HttpOnly cookie. If that failed we start with an empty envelope
  // and `loading=true` so the client can re-try (and the SIMULATED badge
  // explains why the list is empty).
  const [envelope, setEnvelope] = React.useState<CallListEnvelope | null>(initial)
  const [loading, setLoading] = React.useState<boolean>(initial === null)
  const [selectedId, setSelectedId] = React.useState<string | null>(
    (requestedLead && initial?.items.some((r) => r.lead_id === requestedLead) ? requestedLead : null)
      ?? initial?.items[0]?.lead_id
      ?? null,
  )
  const [history, setHistory] = React.useState<HistoryRow[]>([])
  const [historyLoading, setHistoryLoading] = React.useState(false)
  // Bumped after a log/undo so the timeline refetches even when the same lead stays selected.
  const [historyTick, setHistoryTick] = React.useState(0)
  const [postingOutcome, setPostingOutcome] = React.useState<OutcomeKind | null>(null)
  const [note, setNote] = React.useState("")
  const [callbackAt, setCallbackAt] = React.useState<string>(plusDaysISO(2))

  const items = React.useMemo(() => envelope?.items ?? [], [envelope])
  // BUG 3 — after logging an outcome the list may re-rank and drop the
  // just-called lead (callback into the future, do_not_call, etc.), but the
  // operator asked to STAY on that broker until they pick another one. Pin
  // the last-seen row for the current `selectedId`; if the live list drops
  // it, we fall back to the pin so the detail panel + history stay visible.
  const [pinned, setPinned] = React.useState<CallRow | null>(null)
  const liveRow = items.find((r) => r.lead_id === selectedId) ?? null
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (liveRow) setPinned(liveRow)
  }, [liveRow])
  const selected = liveRow ?? (pinned && pinned.lead_id === selectedId ? pinned : null)

  // Load the ranked list on mount + whenever health flips back to live.
  const load = React.useCallback(async () => {
    setLoading(true)
    try {
      // Typed client → /api/proxy/tools/call-list from the browser.
      const { data, response } = await api.GET("/tools/call-list", { params: { query: { limit: 25 } } })
      if (!response.ok || !data) {
        setEnvelope({ date: "", items: [] })
        return
      }
      const j = data as unknown as CallListEnvelope
      setEnvelope({ date: j.date ?? "", items: j.items ?? [] })
    } catch (e) {
      setEnvelope({ date: "", items: [] })
      toast.error("Couldn't load the call list", { description: String(e) })
    } finally {
      setLoading(false)
    }
  }, [])

  // Only re-fetch on mount if the SSR shell didn't give us data (e.g. the
  // backend was asleep on first paint). Otherwise the SSR seed is the truth
  // until the user logs an outcome and we replace the envelope ourselves.
  React.useEffect(() => {
    if (initial === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void load()
    }
  }, [initial, load])

  // Auto-pick the first row so desktop users see the detail column filled in.
  // When a `?lead=<id>` deep-link brought us here and that row is in the
  // freshly-loaded list, prefer it over the top row.
  React.useEffect(() => {
    if (!selectedId && items.length > 0) {
      const preferred = requestedLead && items.find((r) => r.lead_id === requestedLead)
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedId(preferred ? preferred.lead_id : items[0].lead_id)
    }
  }, [items, selectedId, requestedLead])

  // Pull history for the selected lead. History is small; refetching on
  // selection keeps the code trivial and the payload cheap.
  React.useEffect(() => {
    if (!selectedId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHistory([])
      return
    }
    let cancelled = false
    ;(async () => {
      setHistoryLoading(true)
      try {
        const { data, response } = await api.GET("/tools/call-list/history", {
          params: { query: { lead_id: selectedId, limit: 20 } },
        })
        if (!response.ok || !data) {
          if (!cancelled) setHistory([])
          return
        }
        if (!cancelled) setHistory((data as { items?: HistoryRow[] }).items ?? [])
      } catch {
        if (!cancelled) setHistory([])
      } finally {
        if (!cancelled) setHistoryLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedId, historyTick])

  const logOutcome = async (kind: OutcomeKind) => {
    if (!selected) return
    setPostingOutcome(kind)
    try {
      const body: Record<string, unknown> = { lead_id: selected.lead_id, outcome: kind }
      if (kind === "callback") body.callback_at = callbackAt || plusDaysISO(2)
      if (note.trim()) body.note = note.trim()
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { data, response } = await api.POST("/tools/call-list/outcome", { body: body as any })
      if (!response.ok || !data) throw new Error(`outcome ${response.status}`)
      const j = data as unknown as CallListEnvelope
      setEnvelope(j)
      // BUG 3 — the operator doesn't want to jump to the next broker after
      // logging an outcome. Keep `selectedId` exactly where it is. If the
      // just-called lead drops out of the ranked list (e.g. callback into
      // the future, do_not_call), the `pinned` fallback above keeps its
      // detail + history on screen until the operator picks another row.
      // Patch the pinned row's `last_outcome` optimistically so the "Last:"
      // badge updates even when the lead drops out of the live items.
      const newLast: LastOutcome = { outcome: kind, logged_at: new Date().toISOString() }
      setPinned((p) => (p && p.lead_id === selected.lead_id ? { ...p, last_outcome: newLast } : p))
      setNote("")
      setHistoryTick((t) => t + 1)
      // Undo action — if the backend handed us the row id, offer a one-click
      // undo that calls DELETE /outcome/{id}. If the undo fails (already gone,
      // etc.) we toast an error and leave the list as-is; the user can refresh.
      const undoableId = j.logged_outcome_id ?? null
      toast.success(`Logged: ${OUTCOME_LABEL[kind]}`, {
        description:
          kind === "callback"
            ? `Returns on ${body.callback_at as string}`
            : `${selected.name} — ${selected.phone}`,
        action: undoableId
          ? {
              label: "Undo",
              onClick: async () => {
                try {
                  const res = await api.DELETE("/tools/call-list/outcome/{outcome_id}", {
                    params: { path: { outcome_id: undoableId } },
                  })
                  if (!res.response.ok || !res.data) throw new Error(`undo ${res.response.status}`)
                  setEnvelope(res.data as unknown as CallListEnvelope)
                  setHistoryTick((t) => t + 1)
                  toast.success("Undone")
                } catch (e) {
                  toast.error("Couldn't undo", { description: String(e) })
                }
              },
            }
          : undefined,
      })
    } catch (e) {
      toast.error("Couldn't log the outcome", { description: String(e) })
    } finally {
      setPostingOutcome(null)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Call List"
        title="Today's ranked calls"
        description="25 brokers your day should start with — ranked by new authority, hot score, open capacity fit and stale relationship. Deterministic; identical inputs give an identical list."
        actions={<BackendBadge live={live} count={items.length} date={envelope?.date} />}
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,460px)]">
        {/* --- ranked list --- */}
        <div className="space-y-5">
          <Panel
            title="Ranked queue"
            description={
              loading
                ? "Loading today's queue…"
                : items.length
                  ? `${items.length} on the list · pick one to see the opener and log an outcome`
                  : "No callable rows right now."
            }
          >
            {loading ? (
              <p className="text-sm text-muted-foreground">Ranking…</p>
            ) : items.length === 0 ? (
              <EmptyState live={live} />
            ) : (
              <ul className="space-y-2">
                {items.map((row, i) => {
                  const isSelected = row.lead_id === selectedId
                  return (
                    <li key={row.lead_id}>
                      <div
                        className={cn(
                          "flex flex-col gap-2 rounded-sm border px-3 py-2.5 transition sm:flex-row sm:items-start",
                          isSelected ? "border-asphalt bg-accent" : "border-border hover:bg-muted",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => setSelectedId(row.lead_id)}
                          className="flex min-w-0 flex-1 items-start gap-3 text-left"
                        >
                          <span className="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-[3px] bg-asphalt font-mono text-[0.7rem] font-bold text-white">
                            {i + 1}
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
                            <div className="mt-1 flex flex-wrap gap-1.5">
                              {row.reasons.map((reason) => (
                                <span
                                  key={reason}
                                  className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-[0.68rem] font-medium text-foreground"
                                >
                                  {reason}
                                </span>
                              ))}
                            </div>
                            {row.last_outcome ? (
                              <div className="mt-1 text-[0.7rem] text-muted-foreground">
                                Last: {OUTCOME_LABEL[row.last_outcome.outcome] ?? row.last_outcome.outcome}
                                {row.last_outcome.logged_at
                                  ? ` · ${new Date(row.last_outcome.logged_at).toLocaleDateString()}`
                                  : ""}
                              </div>
                            ) : null}
                          </div>
                        </button>

                        <div className="flex flex-wrap items-center gap-2 sm:flex-col sm:items-stretch sm:gap-1.5">
                          <a
                            href={`tel:${row.phone}`}
                            className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-safety px-3 text-sm font-semibold text-asphalt hover:opacity-90"
                          >
                            <Phone className="size-4" />
                            <span className="font-mono">{row.phone}</span>
                          </a>
                          {row.primary_email ? (
                            <Link
                              href={`/emails/compose?lead=${encodeURIComponent(row.lead_id)}`}
                              className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-muted"
                            >
                              <Mail className="size-4" />
                              Email
                            </Link>
                          ) : (
                            <span
                              className="inline-flex h-9 cursor-not-allowed items-center gap-1.5 rounded-sm border border-dashed border-border px-3 text-sm text-muted-foreground"
                              title="No email on file for this lead"
                            >
                              <Mail className="size-4" />
                              No email
                            </span>
                          )}
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </Panel>
        </div>

        {/* --- detail column (fixed on desktop, stacks below on mobile) --- */}
        <div className="space-y-4 xl:sticky xl:top-20 xl:self-start">
          <Panel
            title={
              <span className="inline-flex items-center gap-2">
                <Sparkles className="size-4 text-chart-2" /> Opener &amp; call
              </span>
            }
            description={
              selected
                ? `Ready for ${selected.name}`
                : "Pick a row to see the script and outcome buttons."
            }
          >
            {!selected ? (
              <p className="text-sm text-muted-foreground">
                Pick a broker from the list. The opener script and the call/outcome
                buttons appear here so you never leave this page.
              </p>
            ) : (
              <div className="space-y-4">
                <div>
                  <div className="text-[0.7rem] font-semibold tracking-wider text-muted-foreground uppercase">
                    Opener
                  </div>
                  <p className="mt-1 rounded-sm border border-border bg-background p-2.5 text-sm leading-relaxed">
                    {selected.opener}
                  </p>
                </div>

                <div className="flex flex-wrap gap-1.5">
                  {selected.reasons.map((reason) => (
                    <span
                      key={reason}
                      className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-[0.68rem] font-medium"
                    >
                      {reason}
                    </span>
                  ))}
                </div>

                <a
                  href={`tel:${selected.phone}`}
                  className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-sm bg-safety text-base font-bold text-asphalt hover:opacity-90"
                >
                  <PhoneCall className="size-5" />
                  Call {selected.phone}
                </a>

                <div className="space-y-2">
                  <Label htmlFor="call-note">Note (optional)</Label>
                  <Textarea
                    id="call-note"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    placeholder="What was said — one line, for tomorrow's you."
                  />
                </div>

                <div className="space-y-2">
                  <div className="text-[0.7rem] font-semibold tracking-wider text-muted-foreground uppercase">
                    Log outcome
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Button
                      onClick={() => void logOutcome("booked")}
                      disabled={postingOutcome !== null}
                      className="min-h-10 justify-start font-semibold"
                    >
                      <CheckCircle2 className="size-4" /> Booked
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void logOutcome("no_answer")}
                      disabled={postingOutcome !== null}
                      className="min-h-10 justify-start font-semibold"
                    >
                      <PhoneOff className="size-4" /> No answer
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void logOutcome("not_interested")}
                      disabled={postingOutcome !== null}
                      className="min-h-10 justify-start font-semibold"
                    >
                      <XCircle className="size-4" /> Not interested
                    </Button>
                    <div className="flex items-stretch gap-1.5">
                      <Button
                        variant="outline"
                        onClick={() => void logOutcome("callback")}
                        disabled={postingOutcome !== null}
                        className="min-h-10 flex-1 justify-start font-semibold"
                      >
                        <CalendarClock className="size-4" /> Call back
                      </Button>
                    </div>
                    <div className="col-span-2">
                      <Label htmlFor="callback-at" className="text-[0.7rem] text-muted-foreground">
                        Call back on
                      </Label>
                      <Input
                        id="callback-at"
                        type="date"
                        value={callbackAt}
                        min={plusDaysISO(0)}
                        onChange={(e) => setCallbackAt(e.target.value)}
                      />
                    </div>
                  </div>
                </div>
              </div>
            )}
          </Panel>

          {selected ? (
            <Panel
              title="Call history"
              description={`Recent outcomes for ${selected.name}`}
            >
              {historyLoading ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
              ) : history.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No previous outcomes — this is your first call to this broker.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {history.map((h) => (
                    <li
                      key={h.id}
                      className="flex items-start gap-2 rounded-sm border border-border bg-background px-2.5 py-1.5 text-xs"
                    >
                      <span className="font-semibold">
                        {OUTCOME_LABEL[h.outcome] ?? h.outcome}
                      </span>
                      <span className="text-muted-foreground">
                        {new Date(h.logged_at).toLocaleString()}
                      </span>
                      {h.callback_at ? (
                        <span className="text-muted-foreground">
                          · returns {h.callback_at}
                        </span>
                      ) : null}
                      {h.note ? (
                        <span className="ml-auto max-w-[60%] truncate text-muted-foreground">
                          &ldquo;{h.note}&rdquo;
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : null}
        </div>
      </div>
    </>
  )
}

// ---- little pieces ----------------------------------------------------------

function BackendBadge({
  live,
  count,
  date,
}: {
  live: boolean | null
  count: number
  date: string | undefined
}) {
  const badge =
    live === null
      ? { label: "Checking…", cls: "bg-muted text-muted-foreground" }
      : live
        ? { label: "LIVE", cls: "bg-good text-white" }
        : { label: "SIMULATED", cls: "bg-warn text-asphalt" }
  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className={cn(
          "inline-flex items-center rounded-[3px] px-2 py-0.5 text-[0.68rem] font-bold tracking-wider",
          badge.cls,
        )}
      >
        {badge.label}
      </span>
      <span className="text-muted-foreground">
        {live === false
          ? "Backend asleep — the list is empty until it wakes."
          : date
            ? `${count} rows for ${date}`
            : `${count} rows`}
      </span>
    </div>
  )
}

function EmptyState({ live }: { live: boolean | null }) {
  if (live === false) {
    return (
      <p className="text-sm text-muted-foreground">
        Backend is asleep. Once it wakes (a few seconds on Render&apos;s free tier),
        today&apos;s ranked list will appear here — we don&apos;t fake it.
      </p>
    )
  }
  return (
    <p className="text-sm text-muted-foreground">
      Quiet today — fewer than 25 callable brokers matched. Run the crawler from the{" "}
      <Link href="/" className="underline">
        engine
      </Link>{" "}
      to bring in fresh leads.
    </p>
  )
}
