"use client"

/**
 * Broker Check — /vetting.
 *
 * One input (MC or DOT), one GET to the backend, three panels: verdict +
 * red flags, FMCSA authority block, LJM contact history. Deep-links to
 * the broker detail page when the lookup matched a lead in our store.
 */

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { AlertTriangle, CheckCircle2, Shield, ShieldAlert, ShieldCheck } from "lucide-react"

import { PageHeader, Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ApiRequestError } from "@/lib/api/client"
import { vetBroker, type VetReport, type VetVerdict } from "@/lib/api/vetting"

const VERDICT_COPY: Record<VetVerdict, { label: string; className: string; Icon: typeof Shield }> = {
  safe: { label: "Safe to haul", className: "border-good text-good bg-good/5", Icon: ShieldCheck },
  caution: {
    label: "Proceed with caution",
    className: "border-safety text-safety bg-safety/5",
    Icon: Shield,
  },
  avoid: { label: "Avoid — red flags present", className: "border-bad text-bad bg-bad/5", Icon: ShieldAlert },
}

export default function VettingPage() {
  const [query, setQuery] = useState("")
  const [report, setReport] = useState<VetReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Elapsed seconds so the user sees forward motion instead of a silent
  // 15-second spinner. Backend budget is ~5s now; anything over that is
  // network / cold start, not an app bug.
  const [elapsed, setElapsed] = useState(0)
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!loading) {
      if (tickRef.current) clearInterval(tickRef.current)
      tickRef.current = null
      return
    }
    const started = Date.now()
    tickRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000))
    }, 250)
    return () => {
      if (tickRef.current) clearInterval(tickRef.current)
    }
  }, [loading])

  async function onSubmit(ev: React.FormEvent) {
    ev.preventDefault()
    const key = query.trim()
    if (!key) return
    setLoading(true)
    setError(null)
    setReport(null)
    setElapsed(0)
    try {
      const r = await vetBroker(key)
      setReport(r)
    } catch (e) {
      if (e instanceof ApiRequestError) {
        if (e.status === 404) {
          setError("That MC or DOT isn't on file and FMCSA has no record for it.")
        } else if (e.status === 422) {
          setError("Enter a numeric MC or DOT (prefix MC-/DOT- optional).")
        } else if (e.status === 503 || e.status === 504) {
          setError("FMCSA is slow or offline right now — no cached snapshot to show. Try again in a moment.")
        } else {
          setError(`Lookup failed (${e.status}).`)
        }
      } else {
        setError("Lookup failed — network error.")
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <PageHeader
        eyebrow="Broker Check"
        title="Vet a broker before you haul"
        description="FMCSA authority + LJM contact history on one screen. No paywall, no guesswork."
      />

      <Panel title="Look up a broker" description="Enter an MC or DOT number.">
        <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row">
          <Input
            value={query}
            onChange={(ev) => setQuery(ev.target.value)}
            placeholder="MC-123456 or DOT 9876543"
            className="flex-1"
            aria-label="MC or DOT"
          />
          <Button type="submit" disabled={loading || !query.trim()}>
            {loading ? `Checking FMCSA…${elapsed > 1 ? ` ${elapsed}s` : ""}` : "Check"}
          </Button>
        </form>
        {loading && elapsed >= 4 ? (
          <p className="mt-3 text-xs text-muted-foreground">
            FMCSA is responding slowly — we&apos;ll fall back to the on-file snapshot if the live call times out.
          </p>
        ) : null}
        {error ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-bad">
            <AlertTriangle className="size-4" aria-hidden /> {error}
          </p>
        ) : null}
      </Panel>

      {report ? <ReportView report={report} /> : null}
    </div>
  )
}

function ReportView({ report }: { report: VetReport }) {
  const verdict = VERDICT_COPY[report.verdict]
  const Icon = verdict.Icon
  return (
    <div className="flex flex-col gap-6">
      <section
        className={`flex items-start gap-3 rounded-sm border px-4 py-3 ${verdict.className}`}
        aria-label="Verdict"
      >
        <Icon className="mt-0.5 size-5 shrink-0" aria-hidden />
        <div className="min-w-0">
          <h2 className="font-display text-lg leading-tight font-semibold">{verdict.label}</h2>
          <p className="mt-1 text-sm">
            {report.legal_name || report.dba_name || "Unknown carrier"}
            {report.mc ? ` · MC ${report.mc}` : ""}
            {report.dot ? ` · DOT ${report.dot}` : ""}
          </p>
          {report.stale ? (
            <p className="mt-1 text-xs opacity-80">
              {report.authority.source === "lead_record"
                ? "FMCSA unavailable — showing what we have on file for this carrier; authority not verified"
                : "Cached snapshot — FMCSA was unreachable"}
              {report.snapshot_as_of
                ? ` (as of ${new Date(report.snapshot_as_of).toLocaleDateString()})`
                : report.authority.add_date
                  ? ` (as of ${report.authority.add_date})`
                  : ""}
              .
            </p>
          ) : null}
        </div>
      </section>

      {report.red_flags.length > 0 ? (
        <Panel title="Red flags" description="One line each — read before you dispatch.">
          <ul className="flex flex-col gap-2">
            {report.red_flags.map((flag) => (
              <li
                key={flag.code}
                className="flex items-start gap-2 rounded-sm border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad"
              >
                <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span>
                  <span className="font-mono text-xs opacity-70">{flag.code}</span>
                  <span className="ml-2">{flag.reason}</span>
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : (
        <Panel title="Red flags">
          <p className="flex items-center gap-2 text-sm text-good">
            <CheckCircle2 className="size-4" aria-hidden /> No red flags on record.
          </p>
        </Panel>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="FMCSA authority">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Status</dt>
            <dd>{report.authority.status ?? "—"}</dd>
            <dt className="text-muted-foreground">Added</dt>
            <dd>{report.authority.add_date ?? "—"}</dd>
            <dt className="text-muted-foreground">Age (days)</dt>
            <dd>{report.authority.age_days ?? "—"}</dd>
            <dt className="text-muted-foreground">Out-of-service</dt>
            <dd>{report.authority.oos_date ?? "No record"}</dd>
          </dl>
          {report.evidence_url ? (
            <p className="mt-3 text-xs">
              <a
                className="underline hover:text-foreground"
                href={report.evidence_url}
                target="_blank"
                rel="noreferrer"
              >
                FMCSA source snapshot
              </a>
            </p>
          ) : null}
        </Panel>

        <Panel title="LJM history">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Last contacted</dt>
            <dd>
              {report.prior.last_sent_at
                ? new Date(report.prior.last_sent_at).toLocaleString()
                : "Never"}
            </dd>
            <dt className="text-muted-foreground">Booked</dt>
            <dd>{report.prior.booked_count}</dd>
            <dt className="text-muted-foreground">Rejected</dt>
            <dd>{report.prior.rejected_count}</dd>
            <dt className="text-muted-foreground">Suppression</dt>
            <dd>{report.suppressed ? "Suppressed — do not email" : "Clean"}</dd>
          </dl>
          {report.lead_id ? (
            <p className="mt-3 text-xs">
              <Link
                href={`/brokers/${encodeURIComponent(report.lead_id)}`}
                className="underline hover:text-foreground"
              >
                Open broker detail →
              </Link>
            </p>
          ) : null}
        </Panel>
      </div>
    </div>
  )
}
