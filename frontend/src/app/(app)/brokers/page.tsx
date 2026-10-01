"use client"

/**
 * Brokers — /brokers  (real-data v1)
 *
 * Lists real broker leads from the typed `lib/api/brokers` client. Each row
 * shows a click-to-call/mailto phone + email, a `next_action` chip computed
 * server-side (deterministic rule table in
 * `backend/app/pipeline/broker_next_action.py`), and a one-line reason.
 *
 * No faked contact data — missing phones/emails render as "—" instead of a
 * placeholder. The chip reason tells the operator *why* an action is next so
 * they never have to guess.
 *
 * Pattern: typed openapi-fetch client in `lib/api/`, `NEXT_PUBLIC_API_URL`,
 * bearer injected by `apiFetch` — same as the shippers page.
 */

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"
import { Mail, Phone, Search } from "lucide-react"
import { PageHeader, Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import {
  listBrokers,
  type BrokerRow,
  type NextActionKind,
} from "@/lib/api/brokers"

const ACTION_LABEL: Record<NextActionKind, string> = {
  call: "Call",
  email: "Email",
  follow_up: "Follow up",
  wait: "Wait",
}

const ACTION_STYLE: Record<NextActionKind, string> = {
  call: "bg-bad text-white",
  email: "bg-chart-2 text-white",
  follow_up: "bg-warn text-asphalt",
  wait: "bg-muted text-muted-foreground",
}

const ACTION_FILTERS: Array<NextActionKind | "all"> = [
  "all",
  "call",
  "email",
  "follow_up",
  "wait",
]

export default function BrokersPage() {
  const [rows, setRows] = React.useState<BrokerRow[]>([])
  const [total, setTotal] = React.useState(0)
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [loadingMore, setLoadingMore] = React.useState(false)
  const [state, setState] = React.useState<string>("")
  const [minFit, setMinFit] = React.useState<number>(0)
  const [hasEmail, setHasEmail] = React.useState<boolean>(false)
  const [hasPhone, setHasPhone] = React.useState<boolean>(false)
  const [action, setAction] = React.useState<NextActionKind | "all">("all")
  const [q, setQ] = React.useState("")

  // Load list from the typed client. We re-run on filter changes and on
  // `/brokers` first mount; `cursor` is only read when `append=true`.
  const load = React.useCallback(
    async (append: boolean, cursorOverride?: string | null) => {
      if (append) setLoadingMore(true)
      else setLoading(true)
      try {
        const data = await listBrokers({
          state: state || undefined,
          min_fit: minFit > 0 ? minFit : undefined,
          has_email: hasEmail || undefined,
          has_phone: hasPhone || undefined,
          next_action: action === "all" ? undefined : action,
          q: q.trim() || undefined,
          cursor: append ? (cursorOverride ?? undefined) : undefined,
          limit: 50,
        })
        setRows((prev) => (append ? [...prev, ...data.items] : data.items))
        setCursor(data.next_cursor ?? null)
        setTotal(data.total)
      } catch (e) {
        toast.error("Couldn't load brokers", { description: String(e) })
      } finally {
        setLoading(false)
        setLoadingMore(false)
      }
    },
    [state, minFit, hasEmail, hasPhone, action, q],
  )

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, minFit, hasEmail, hasPhone, action])

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault()
    void load(false)
  }

  return (
    <>
      <PageHeader
        eyebrow="Brokers"
        title="Broker database"
        description="Real broker leads with a deterministic next-action chip per row. The reason is in the tooltip — no AI, no magic."
      />

      <div className="mb-4 flex flex-wrap items-end gap-2 rounded-sm border border-border bg-card p-3">
        <form onSubmit={onSearch} className="flex min-w-0 flex-1 items-end gap-2">
          <div className="min-w-0 flex-1">
            <Label htmlFor="q" className="text-[0.7rem] text-muted-foreground">
              Search name / MC / DOT
            </Label>
            <Input
              id="q"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Acme, MC-123…"
            />
          </div>
          <Button type="submit" variant="outline" className="h-10">
            <Search className="size-4" /> Search
          </Button>
        </form>
        <div>
          <Label htmlFor="state" className="text-[0.7rem] text-muted-foreground">
            State
          </Label>
          <Input
            id="state"
            maxLength={2}
            value={state}
            onChange={(e) => setState(e.target.value.toUpperCase())}
            placeholder="NJ"
            className="w-20"
          />
        </div>
        <div>
          <Label htmlFor="min-fit" className="text-[0.7rem] text-muted-foreground">
            Min fit
          </Label>
          <Input
            id="min-fit"
            type="number"
            min={0}
            max={100}
            value={minFit}
            onChange={(e) => setMinFit(Number(e.target.value) || 0)}
            className="w-20"
          />
        </div>
        <div className="flex items-end gap-3 text-xs">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={hasPhone}
              onChange={(e) => setHasPhone(e.target.checked)}
            />
            Has phone
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={hasEmail}
              onChange={(e) => setHasEmail(e.target.checked)}
            />
            Has email
          </label>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {ACTION_FILTERS.map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => setAction(kind)}
            className={cn(
              "inline-flex items-center rounded-[3px] border px-2.5 py-1 text-xs font-semibold tracking-wide uppercase transition",
              action === kind
                ? "border-asphalt bg-asphalt text-white"
                : "border-border bg-background hover:bg-muted",
            )}
          >
            {kind === "all" ? "All" : ACTION_LABEL[kind]}
          </button>
        ))}
        <span className="ml-auto self-center text-xs text-muted-foreground">
          {total} broker{total === 1 ? "" : "s"}
        </span>
      </div>

      <Panel
        title="Brokers"
        description={
          loading
            ? "Loading…"
            : total === 0
              ? "No real brokers yet — run the FMCSA crawler from Settings → Crawlers."
              : "Click a row to open the full contact block."
        }
      >
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading real broker leads…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing matches these filters. Clear them or run the FMCSA crawler to pull in fresh rows — we don&apos;t fake data here.
          </p>
        ) : (
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.id}>
                <div className="flex flex-col gap-2 rounded-sm border border-border px-3 py-2.5 transition hover:bg-muted sm:flex-row sm:items-start">
                  <Link
                    href={`/brokers/${encodeURIComponent(r.id)}`}
                    className="min-w-0 flex-1"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-semibold">{r.name}</span>
                      <span className="font-mono text-[0.68rem] text-muted-foreground">
                        {r.mc ? `MC-${r.mc}` : r.dot ? `DOT-${r.dot}` : "—"}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {r.city ? `${r.city}, ` : ""}
                        {r.state}
                      </span>
                      {r.fit_score != null ? (
                        <span className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-[0.68rem] font-medium">
                          fit {r.fit_score}
                        </span>
                      ) : null}
                      <span
                        className={cn(
                          "ml-auto inline-flex items-center rounded-[3px] px-2 py-0.5 text-[0.68rem] font-bold tracking-wider uppercase",
                          ACTION_STYLE[r.next_action.kind],
                        )}
                        title={r.next_action.reason}
                      >
                        {ACTION_LABEL[r.next_action.kind]}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {r.next_action.reason}
                    </div>
                  </Link>
                  <div className="flex flex-wrap items-center gap-1.5 sm:flex-col sm:items-stretch">
                    {r.phone.value ? (
                      <a
                        href={`tel:${r.phone.value}`}
                        className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-safety px-3 text-sm font-semibold text-asphalt hover:opacity-90"
                      >
                        <Phone className="size-4" />
                        <span className="font-mono">{r.phone.value}</span>
                      </a>
                    ) : (
                      <span className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-dashed border-border px-3 text-sm text-muted-foreground">
                        <Phone className="size-4" />—
                      </span>
                    )}
                    {r.primary_email.value ? (
                      <a
                        href={`mailto:${r.primary_email.value}`}
                        className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-muted"
                      >
                        <Mail className="size-4" />
                        <span className="max-w-[180px] truncate">{r.primary_email.value}</span>
                      </a>
                    ) : (
                      <span className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-dashed border-border px-3 text-sm text-muted-foreground">
                        <Mail className="size-4" />—
                      </span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {cursor ? (
          <div className="mt-4 flex justify-center">
            <Button
              type="button"
              variant="outline"
              disabled={loadingMore}
              onClick={() => void load(true, cursor)}
            >
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
      </Panel>
    </>
  )
}
