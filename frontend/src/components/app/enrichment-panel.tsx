"use client"

/**
 * EnrichmentPanel — on-demand enrichment surface for a shipper candidate
 * (pre-promotion) or a lead (post-promotion).
 *
 * Reads and writes through the typed `lib/api/enrichment` module — no `/api/*`
 * proxy, no hand-typed shapes. Deliberately minimal: one "Enrich now" button,
 * a list of decision-makers, a list of website contacts, and a Fit-score badge.
 *
 * Save-everything doctrine: this component NEVER hides a sighting. All
 * decision-makers + contacts we've stored are shown, even if the current run
 * added zero new rows.
 */

import { useEffect, useState } from "react"
import {
  enrichCandidate,
  enrichLead,
  getEnrichment,
  type EnrichmentResult,
} from "@/lib/api/enrichment"

export function EnrichmentPanel({
  leadId,
  candidateId,
}: {
  leadId: string | null
  candidateId: string
}) {
  const [state, setState] = useState<EnrichmentResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!leadId) return
    let alive = true
    getEnrichment(leadId)
      .then((v) => {
        if (alive) setState(v)
      })
      .catch(() => {
        if (alive) setState(null)
      })
    return () => {
      alive = false
    }
  }, [leadId, candidateId])

  async function runEnrich() {
    setBusy(true)
    setError(null)
    try {
      const res = leadId ? await enrichLead(leadId, { force: true }) : await enrichCandidate(candidateId, { force: true })
      setState(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : "enrichment failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-sm border border-border p-2">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">Enrichment</div>
        <button
          type="button"
          onClick={runEnrich}
          disabled={busy}
          className="inline-flex h-8 items-center rounded-sm bg-asphalt px-3 text-xs font-semibold text-white disabled:opacity-60"
        >
          {busy ? "Enriching…" : "Enrich now"}
        </button>
      </div>

      {error ? <div className="mt-2 text-xs text-red-600">{error}</div> : null}

      {state ? (
        <div className="mt-2 space-y-2 text-xs">
          <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            <span>Status: {state.status}</span>
            {state.is_js_only_site ? <span className="rounded-sm bg-yellow-100 px-1">JS-only site</span> : null}
            {state.linkedin_company_url ? (
              <a href={state.linkedin_company_url} className="underline" target="_blank" rel="noreferrer">LinkedIn</a>
            ) : null}
            {state.website_url ? (
              <a href={state.website_url} className="underline" target="_blank" rel="noreferrer">Website</a>
            ) : null}
          </div>

          <div>
            <div className="font-semibold">Decision-makers ({state.decision_makers.length})</div>
            <ul className="mt-1 space-y-1">
              {state.decision_makers.map((d) => (
                <li key={d.id} className="flex flex-wrap items-center gap-2 rounded-sm bg-muted/40 p-1">
                  <span className="font-medium">{d.name ?? "(no name)"}</span>
                  <span className="text-muted-foreground">{d.title ?? ""}</span>
                  {d.linkedin_url ? (
                    <a href={d.linkedin_url} target="_blank" rel="noreferrer" className="underline">
                      LinkedIn
                    </a>
                  ) : null}
                  {d.email ? (
                    <a href={`mailto:${d.email}`} className="underline">
                      {d.email}
                    </a>
                  ) : (
                    <span
                      className="cursor-help text-muted-foreground"
                      title="no published email"
                    >
                      (no email)
                    </span>
                  )}
                  {leadId && d.email ? (
                    <a
                      href={`/emails/compose?lead=${encodeURIComponent(leadId)}&contact=${d.id}`}
                      className="rounded-sm bg-safety px-2 py-0.5 text-asphalt"
                    >
                      Draft email
                    </a>
                  ) : (
                    <span className="opacity-50" title="no published email">
                      Draft email
                    </span>
                  )}
                </li>
              ))}
              {state.decision_makers.length === 0 ? (
                <li className="text-muted-foreground">None yet.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <div className="font-semibold">Website contacts ({state.contacts.length})</div>
            <ul className="mt-1 space-y-1">
              {state.contacts.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-2 rounded-sm bg-muted/40 p-1">
                  {c.name ? <span className="font-medium">{c.name}</span> : null}
                  {c.email ? (
                    <a href={`mailto:${c.email}`} className="underline">
                      {c.email}
                    </a>
                  ) : null}
                  {c.phone ? (
                    <a href={`tel:${c.phone}`} className="font-mono underline">
                      {c.phone}
                    </a>
                  ) : null}
                  {leadId && c.email ? (
                    <a
                      href={`/emails/compose?lead=${encodeURIComponent(leadId)}&contact=${c.id}`}
                      className="rounded-sm bg-safety px-2 py-0.5 text-asphalt"
                    >
                      Draft email
                    </a>
                  ) : null}
                </li>
              ))}
              {state.contacts.length === 0 ? <li className="text-muted-foreground">None yet.</li> : null}
            </ul>
          </div>

          {typeof state.fit_score === "number" ? (
            <div>
              <div className="font-semibold">Fit {state.fit_score} / 100</div>
              <ul className="mt-1 list-disc pl-5">
                {(state.fit_reasons ?? []).map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-2 text-xs text-muted-foreground">
          Click Enrich now to look up public decision-makers + website contacts.
        </div>
      )}
    </div>
  )
}
