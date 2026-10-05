"use client"

/**
 * Brokers overview table — real-data v2 restore of the pre-ba15198 surface.
 *
 * Dense table with segment pills on top, column-click sort with arrow
 * indicators (user's explicit extra: "when I click on the labels it should be
 * sorted"), a 12-month activity sparkline, and the real-data next-action
 * chip from the current list route carried over as the "Suggested action"
 * column.
 *
 * Two columns are deliberately HIDDEN per the operator's plan-gate decision:
 *   - Revenue  — Load table has no FK to Lead; dashed "—" would misrepresent.
 *   - Payment issues chip — signal is still computed server-side on
 *     overview.has_bounce / has_suppression, but we don't render a column.
 *
 * Pattern: typed openapi-fetch client in `lib/api/brokers`, no ad-hoc proxies.
 */

import * as React from "react"
import Link from "next/link"
import { ArrowDown, ArrowUp, ArrowUpDown, Download, Search } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { HealthPill, Sparkline } from "@/components/app/ui"
import { cn } from "@/lib/utils"
import { pct } from "@/lib/format"
import {
  listBrokers,
  type BrokerRow,
  type BrokerSegment,
  type BrokerSortKey,
  type NextActionKind,
  type SegmentsCount,
} from "@/lib/api/brokers"

type SegmentChoice = BrokerSegment | "all"

const SEGMENT_LABEL: Record<SegmentChoice, string> = {
  all: "All",
  hot: "Hot",
  warm: "Warm",
  payment_issues: "Payment issues",
  dormant: "Dormant",
  not_interested: "Not interested",
  neutral: "Neutral",
}

const SEGMENT_ORDER: SegmentChoice[] = [
  "all", "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral",
]

const SEGMENT_DOT: Record<SegmentChoice, string> = {
  all: "var(--muted-foreground)",
  hot: "var(--bad)",
  warm: "var(--chart-2)",
  payment_issues: "var(--warn)",
  dormant: "var(--steel)",
  not_interested: "var(--muted)",
  neutral: "var(--chart-1)",
}

const ACTION_LABEL: Record<NextActionKind, string> = {
  call: "Call", email: "Email", follow_up: "Follow up", wait: "Wait",
}
const ACTION_STYLE: Record<NextActionKind, string> = {
  call: "bg-bad text-white",
  email: "bg-chart-2 text-white",
  follow_up: "bg-warn text-asphalt",
  wait: "bg-muted text-muted-foreground",
}

const ACTIVITY_MONTHS = [12, 7, 3] as const
type ActivityMonths = (typeof ACTIVITY_MONTHS)[number]

type SortDir = "asc" | "desc"
type HeaderSort = { key: BrokerSortKey; dir: SortDir }

/** Default direction when a header is first clicked. "Health 100 → 0" is more
 * useful than ascending; "Days since 0 → N" (most-recent first) is likewise.
 * Encoded so the SortHeader stays simple. */
const DEFAULT_DIR: Record<BrokerSortKey, SortDir> = {
  health: "desc",
  win_rate: "desc",
  booked: "desc",
  rejected: "desc",
  days_since: "asc",
  name: "asc",
}

export function BrokersTable() {
  const [rows, setRows] = React.useState<BrokerRow[]>([])
  const [total, setTotal] = React.useState(0)
  const [segmentsCount, setSegmentsCount] = React.useState<SegmentsCount | null>(null)
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [loadingMore, setLoadingMore] = React.useState(false)

  const [segment, setSegment] = React.useState<SegmentChoice>("all")
  const [action, setAction] = React.useState<NextActionKind | "all">("all")
  const [state, setState] = React.useState("")
  const [minFit, setMinFit] = React.useState(0)
  const [hasEmail, setHasEmail] = React.useState(false)
  const [hasPhone, setHasPhone] = React.useState(false)
  const [q, setQ] = React.useState("")

  const [headerSort, setHeaderSort] = React.useState<HeaderSort | null>({ key: "health", dir: "desc" })
  const [activityMonths, setActivityMonths] = React.useState<ActivityMonths>(12)
  const [exporting, setExporting] = React.useState(false)

  // Server-side sort / segment via query params; we rely on the backend's
  // deterministic order so "load more" pagination stays stable.
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
          include: "overview_metrics",
          segment,
          sort: headerSort?.key,
        })
        setRows((prev) => (append ? [...prev, ...data.items] : data.items))
        setCursor(data.next_cursor ?? null)
        setTotal(data.total)
        setSegmentsCount(data.segments_count ?? null)
      } catch (e) {
        toast.error("Couldn't load brokers", { description: String(e) })
      } finally {
        setLoading(false)
        setLoadingMore(false)
      }
    },
    [state, minFit, hasEmail, hasPhone, action, q, segment, headerSort],
  )

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, minFit, hasEmail, hasPhone, action, segment, headerSort])

  // Client-side search filter — tiny over a 50-row page; keeps the UX snappy
  // without a server round-trip on every keystroke.
  const shown = React.useMemo(() => {
    if (!q.trim()) return rows
    const needle = q.toLowerCase()
    return rows.filter((r) =>
      [r.name, r.mc ?? "", r.dot ?? "", r.city ?? "", r.state].some((f) =>
        f.toLowerCase().includes(needle),
      ),
    )
  }, [rows, q])

  // 1st click: default dir. 2nd: flip. 3rd: clear (back to server default).
  const cycleHeader = (key: BrokerSortKey) => {
    setHeaderSort((cur) => {
      if (!cur || cur.key !== key) return { key, dir: DEFAULT_DIR[key] }
      const flipped = cur.dir === "asc" ? "desc" : "asc"
      if (flipped === DEFAULT_DIR[key]) return null
      return { key, dir: flipped }
    })
  }

  // Client-side flip for the "asc" direction — the backend always returns a
  // sort DIRECTION of descending-good (health high→low, days-since low→high);
  // if the user wants the inverse, we just reverse the array on the client.
  const sorted = React.useMemo(() => {
    if (!headerSort) return shown
    if (headerSort.dir === DEFAULT_DIR[headerSort.key]) return shown
    return [...shown].reverse()
  }, [shown, headerSort])

  const downloadCsv = async () => {
    setExporting(true)
    try {
      const qs = new URLSearchParams()
      if (segment && segment !== "all") qs.set("segment", segment)
      if (state) qs.set("state", state)
      if (minFit > 0) qs.set("min_fit", String(minFit))
      if (hasEmail) qs.set("has_email", "true")
      if (hasPhone) qs.set("has_phone", "true")
      if (action !== "all") qs.set("next_action", action)
      if (q.trim()) qs.set("q", q.trim())
      if (headerSort) qs.set("sort", headerSort.key)
      const res = await fetch(`/api/proxy/brokers.csv?${qs.toString()}`)
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `brokers-${segment}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      toast.error("CSV export failed", { description: String(e) })
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="rounded-sm border border-border bg-card">
      {/* ---- Segment pills --------------------------------------------- */}
      <div className="flex flex-wrap gap-1 border-b border-border p-2">
        {SEGMENT_ORDER.map((sg) => {
          const n = segmentsCount?.[sg] ?? (sg === "all" ? total : 0)
          return (
            <button
              key={sg}
              type="button"
              onClick={() => setSegment(sg)}
              className={cn(
                "flex h-8 items-center gap-2 rounded-sm px-3 text-sm font-medium transition-colors",
                segment === sg ? "bg-asphalt text-white" : "hover:bg-muted",
              )}
            >
              {sg !== "all" ? (
                <span className="size-2 rounded-[2px]" style={{ background: SEGMENT_DOT[sg] }} aria-hidden />
              ) : null}
              {SEGMENT_LABEL[sg]}
              <span
                className={cn(
                  "num font-mono text-xs",
                  segment === sg ? "text-[#b9bcc2]" : "text-muted-foreground",
                )}
              >
                {n}
              </span>
            </button>
          )
        })}
      </div>

      {/* ---- Filter bar ------------------------------------------------ */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search broker, city, MC / DOT…"
            className="pl-8"
            aria-label="Search brokers"
          />
        </div>
        <div className="flex items-end gap-2">
          <div>
            <Label htmlFor="state" className="text-[0.68rem] text-muted-foreground">State</Label>
            <Input
              id="state"
              maxLength={2}
              value={state}
              onChange={(e) => setState(e.target.value.toUpperCase())}
              placeholder="NJ"
              className="w-16"
            />
          </div>
          <div>
            <Label htmlFor="min-fit" className="text-[0.68rem] text-muted-foreground">Min fit</Label>
            <Input
              id="min-fit"
              type="number"
              min={0}
              max={100}
              value={minFit}
              onChange={(e) => setMinFit(Number(e.target.value) || 0)}
              className="w-16"
            />
          </div>
          <label className="flex items-center gap-1 pb-1 text-xs">
            <input type="checkbox" checked={hasPhone} onChange={(e) => setHasPhone(e.target.checked)} />
            Phone
          </label>
          <label className="flex items-center gap-1 pb-1 text-xs">
            <input type="checkbox" checked={hasEmail} onChange={(e) => setHasEmail(e.target.checked)} />
            Email
          </label>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-sm text-muted-foreground">{total} brokers</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void downloadCsv()}
            disabled={exporting || total === 0}
          >
            <Download className="size-4" /> {exporting ? "Exporting…" : "Export CSV"}
          </Button>
        </div>
      </div>

      {/* ---- Next-action chip filters (secondary row) ----------------- */}
      <div className="flex flex-wrap gap-1 border-b border-border px-3 py-2">
        {(["all", "call", "email", "follow_up", "wait"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setAction(k)}
            className={cn(
              "inline-flex h-7 items-center rounded-[3px] border px-2 text-[0.7rem] font-semibold tracking-wide uppercase transition",
              action === k ? "border-asphalt bg-asphalt text-white" : "border-border bg-background hover:bg-muted",
            )}
          >
            {k === "all" ? "All actions" : ACTION_LABEL[k]}
          </button>
        ))}
      </div>

      {/* ---- Table ----------------------------------------------------- */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>
              <SortHeader label="Broker" k="name" headerSort={headerSort} onClick={cycleHeader} />
            </TableHead>
            <TableHead>
              <SortHeader label="Health" k="health" headerSort={headerSort} onClick={cycleHeader} />
            </TableHead>
            <TableHead className="text-right">
              <SortHeader label="Win rate" k="win_rate" headerSort={headerSort} onClick={cycleHeader} align="right" />
            </TableHead>
            <TableHead className="text-right">
              <SortHeader label="Booked" k="booked" headerSort={headerSort} onClick={cycleHeader} align="right" />
            </TableHead>
            <TableHead className="text-right">
              <SortHeader label="Rejected" k="rejected" headerSort={headerSort} onClick={cycleHeader} align="right" />
            </TableHead>
            <TableHead className="text-right">
              <SortHeader label="Days since" k="days_since" headerSort={headerSort} onClick={cycleHeader} align="right" />
            </TableHead>
            <TableHead className="pr-2">
              <div className="flex items-center justify-between gap-2">
                <span>{activityMonths}-month activity</span>
                <div className="inline-flex overflow-hidden rounded-sm border border-border bg-background">
                  {ACTIVITY_MONTHS.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setActivityMonths(m)}
                      className={cn(
                        "px-1.5 text-[0.65rem] font-medium",
                        m === activityMonths ? "bg-asphalt text-white" : "hover:bg-muted",
                      )}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>
            </TableHead>
            <TableHead>Suggested action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={8} className="py-6 text-center text-sm text-muted-foreground">
                Loading real broker leads…
              </TableCell>
            </TableRow>
          ) : sorted.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="py-6 text-center text-sm text-muted-foreground">
                Nothing matches these filters. Clear them or run the FMCSA crawler to pull in fresh rows.
              </TableCell>
            </TableRow>
          ) : (
            sorted.map((r) => {
              const om = r.overview ?? null
              const series = om
                ? om.monthly_series.slice(-activityMonths).map((p) => p.booked + p.rejected)
                : [0, 0, 0]
              return (
                <TableRow key={r.id}>
                  <TableCell className="max-w-[320px]">
                    <Link
                      href={`/brokers/${encodeURIComponent(r.id)}`}
                      className="block truncate font-semibold hover:underline"
                    >
                      {r.name}
                    </Link>
                    <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                      <span className="font-mono">
                        {r.mc ? `MC-${r.mc}` : r.dot ? `DOT-${r.dot}` : "—"}
                      </span>
                      <span className="truncate">
                        {r.city ? `${r.city}, ` : ""}
                        {r.state}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    {om ? (
                      <HealthPill
                        value={om.health_score}
                        delta={om.health_delta ?? undefined}
                      />
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="num text-right font-mono">
                    {om && om.win_rate !== null && om.win_rate !== undefined
                      ? pct(om.win_rate)
                      : "—"}
                  </TableCell>
                  <TableCell className="num text-right font-mono">
                    {om ? om.booked_12m : "—"}
                  </TableCell>
                  <TableCell className="num text-right font-mono">
                    {om ? om.rejected_12m : "—"}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "num text-right font-mono",
                      om && om.days_since_last_contact !== null && om.days_since_last_contact !== undefined && om.days_since_last_contact > 90 && "text-bad",
                    )}
                  >
                    {om && om.days_since_last_contact !== null && om.days_since_last_contact !== undefined
                      ? `${om.days_since_last_contact}d`
                      : "—"}
                  </TableCell>
                  <TableCell className="pr-2">
                    <Sparkline values={series.length ? series : [0]} />
                  </TableCell>
                  <TableCell>
                    <span
                      className={cn(
                        "inline-flex items-center rounded-[3px] px-2 py-0.5 text-[0.68rem] font-bold tracking-wider uppercase",
                        ACTION_STYLE[r.next_action.kind],
                      )}
                      title={r.next_action.reason}
                    >
                      {ACTION_LABEL[r.next_action.kind]}
                    </span>
                  </TableCell>
                </TableRow>
              )
            })
          )}
        </TableBody>
      </Table>

      {cursor ? (
        <div className="flex justify-center border-t border-border p-3">
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
    </div>
  )
}

/** Clickable, keyboardable sort header — real <button> so tab + enter/space
 * work. The arrow icon is ArrowUpDown when inactive, ArrowDown/ArrowUp when
 * active. */
function SortHeader({
  label,
  k,
  headerSort,
  onClick,
  align = "left",
}: {
  label: string
  k: BrokerSortKey
  headerSort: HeaderSort | null
  onClick: (k: BrokerSortKey) => void
  align?: "left" | "right"
}) {
  const active = headerSort?.key === k
  const dir = active ? headerSort!.dir : null
  const Icon = dir === "desc" ? ArrowDown : dir === "asc" ? ArrowUp : ArrowUpDown
  const ariaLabel = dir
    ? `${label}, sorted ${dir === "desc" ? "descending" : "ascending"}`
    : `${label}, click to sort`
  return (
    <button
      type="button"
      onClick={() => onClick(k)}
      aria-label={ariaLabel}
      className={cn(
        "-mx-1 inline-flex w-full items-center gap-1 rounded-sm px-1 py-0.5 text-left transition-colors hover:text-foreground",
        active ? "text-foreground" : "text-muted-foreground",
        align === "right" && "justify-end text-right",
      )}
    >
      <span>{label}</span>
      <Icon className={cn("size-3.5 shrink-0", active ? "opacity-100" : "opacity-40")} aria-hidden />
    </button>
  )
}
