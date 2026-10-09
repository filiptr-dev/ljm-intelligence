"use client"

/**
 * Follow-ups — /pipeline (client island).
 *
 * Four columns (New → Contacted → Replied → Booked) rendered from derived
 * data on the backend. The only editable surface per card is the note
 * textarea and `next_touch`; "Log call" and "Draft email" deep-link into
 * the existing surfaces (/brokers/[id], /emails/compose).
 *
 * Initial payload comes from the server shell at ./page-shell.tsx so the
 * first paint carries real rows without a client-side spinner.
 */

import { useCallback, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { AlertCircle, Mail, Phone } from "lucide-react"

import { PageHeader, Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { getBoard, saveNote, type Board, type BoardCard } from "@/lib/api/followups"

type ColumnKey = keyof Board

const COLUMNS: { key: ColumnKey; label: string; hint: string }[] = [
  { key: "new", label: "New", hint: "Leads we have not touched yet." },
  { key: "contacted", label: "Contacted", hint: "Email went out — waiting on reply." },
  { key: "replied", label: "Replied", hint: "They came back. Close with a call." },
  { key: "booked", label: "Booked", hint: "Loads moving. Ops owns these now." },
]

export default function PipelineClient({ initial }: { initial: Board | null }) {
  const [board, setBoard] = useState<Board | null>(initial)
  const [error, setError] = useState<string | null>(initial ? null : "No initial board.")
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const b = await getBoard(50)
      setBoard(b)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load board.")
    } finally {
      setLoading(false)
    }
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Follow-ups"
        title="Move leads through your pipeline"
        description="Four columns, driven by data you already generate. Notes + next-touch are the only hand-moved pieces."
        actions={
          <Button onClick={() => void refresh()} disabled={loading} variant="outline">
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {error ? (
        <div className="flex items-center gap-2 rounded-sm border border-bad/40 bg-bad/5 px-3 py-2 text-sm text-bad">
          <AlertCircle className="size-4" aria-hidden /> {error}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {COLUMNS.map((col) => (
          <Column
            key={col.key}
            label={col.label}
            hint={col.hint}
            cards={board ? board[col.key] : []}
            loading={loading && !board}
          />
        ))}
      </div>
    </div>
  )
}

function Column({
  label,
  hint,
  cards,
  loading,
}: {
  label: string
  hint: string
  cards: BoardCard[]
  loading: boolean
}) {
  return (
    <Panel title={`${label} (${cards.length})`} description={hint} bodyClassName="flex flex-col gap-3">
      {loading ? (
        <p className="text-xs text-muted-foreground">Loading…</p>
      ) : cards.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nothing here yet.</p>
      ) : (
        cards.map((card) => <Card key={card.lead_id} card={card} />)
      )}
    </Panel>
  )
}

function Card({ card }: { card: BoardCard }) {
  return (
    <article className="flex flex-col gap-2 rounded-sm border border-border bg-background p-3">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-sm leading-tight">{card.name}</h3>
          <p className="truncate text-xs text-muted-foreground">
            {[card.city, card.state].filter(Boolean).join(", ") || "—"}
            {card.mc ? ` · MC ${card.mc}` : ""}
          </p>
        </div>
        <ActionPill
          kind={card.next_action.kind}
          reason={card.next_action.reason}
          daysWaiting={card.days_waiting}
        />
      </header>

      <NoteEditor card={card} />

      <footer className="mt-1 flex gap-2 text-xs">
        <Link
          href={`/brokers/${encodeURIComponent(card.lead_id)}?action=call`}
          className="inline-flex items-center rounded-sm border border-border px-2 py-1 hover:bg-muted"
        >
          <Phone className="mr-1 size-3.5" aria-hidden /> Log call
        </Link>
        <Link
          href={`/emails/compose?broker=${encodeURIComponent(card.lead_id)}`}
          className="inline-flex items-center rounded-sm border border-border px-2 py-1 hover:bg-muted"
        >
          <Mail className="mr-1 size-3.5" aria-hidden /> Draft email
        </Link>
      </footer>
    </article>
  )
}

function ActionPill({
  kind,
  reason,
  daysWaiting,
}: {
  kind: string
  reason: string
  daysWaiting: number | null | undefined
}) {
  const tone =
    kind === "call"
      ? "border-safety text-safety bg-safety/5"
      : kind === "email"
        ? "border-good text-good bg-good/5"
        : kind === "follow_up"
          ? "border-bad text-bad bg-bad/5"
          : "border-border text-muted-foreground"
  return (
    <span
      className={`shrink-0 rounded-sm border px-2 py-0.5 text-[0.7rem] font-medium ${tone}`}
      title={reason}
    >
      {kind.replace("_", " ")}
      {daysWaiting != null ? ` · ${daysWaiting}d` : ""}
    </span>
  )
}

function NoteEditor({ card }: { card: BoardCard }) {
  const [note, setNote] = useState(card.note ?? "")
  const [nextTouch, setNextTouch] = useState(card.next_touch ?? "")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const persist = useCallback(
    async (noteVal: string, nextVal: string) => {
      setSaving(true)
      try {
        await saveNote(card.lead_id, {
          note: noteVal,
          next_touch: nextVal || null,
        })
        setSaved(true)
        setTimeout(() => setSaved(false), 1200)
      } catch {
        // Error state is subtle on purpose — the user sees the row stay as-is.
      } finally {
        setSaving(false)
      }
    },
    [card.lead_id],
  )

  const schedule = useCallback(
    (noteVal: string, nextVal: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => {
        void persist(noteVal, nextVal)
      }, 600)
    },
    [persist],
  )

  const hint = useMemo(() => {
    if (saving) return "Saving…"
    if (saved) return "Saved"
    return ""
  }, [saving, saved])

  return (
    <div className="flex flex-col gap-1">
      <Textarea
        value={note}
        onChange={(ev) => {
          setNote(ev.target.value)
          schedule(ev.target.value, nextTouch)
        }}
        placeholder="Note (auto-saved)"
        className="min-h-[2.5rem] text-xs"
        rows={2}
      />
      <div className="flex items-center gap-2 text-[0.7rem] text-muted-foreground">
        <label className="flex items-center gap-1">
          <span>Next touch:</span>
          <Input
            type="date"
            value={nextTouch}
            onChange={(ev) => {
              setNextTouch(ev.target.value)
              schedule(note, ev.target.value)
            }}
            className="h-6 w-32 px-1 text-[0.7rem]"
          />
        </label>
        {hint ? <span className="ml-auto">{hint}</span> : null}
      </div>
    </div>
  )
}
