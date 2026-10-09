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
 *
 * ## Why @dnd-kit instead of native HTML5 drag
 *
 * The earlier native `draggable` version worked in some browsers and
 * silently refused to start a drag in others — the usual Chromium
 * suspects (trackpad on macOS, a React re-render inside `onDragStart`
 * aborting the drag, SVG child intercepting the pointerdown). We switched
 * to `@dnd-kit/core` with a `PointerSensor` so every pointing device
 * (mouse, trackpad, touch) goes through the same code path. The grip is
 * still the ONLY handle via `useDraggable`'s `listeners`, so the note
 * textarea and date input stay text-selectable. A 5px activation
 * distance keeps simple clicks on the grip from starting a drag.
 */

import { useCallback, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { AlertCircle, GripVertical, Mail, Phone } from "lucide-react"
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core"

import { PageHeader, Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  getBoard,
  saveNote,
  setStage,
  type Board,
  type BoardCard,
  type Stage,
} from "@/lib/api/followups"

type ColumnKey = keyof Board

const COLUMNS: { key: ColumnKey; label: string; hint: string }[] = [
  { key: "new", label: "New", hint: "Leads we have not touched yet." },
  { key: "contacted", label: "Contacted", hint: "Email went out — waiting on reply." },
  { key: "replied", label: "Replied", hint: "They came back. Close with a call." },
  { key: "booked", label: "Booked", hint: "Loads moving. Ops owns these now." },
]

// Encode "which column is this card currently in" into the draggable id so
// `onDragEnd` can compute the from→to move without a parallel lookup map.
function makeDragId(col: ColumnKey, leadId: string): string {
  return `${col}|${leadId}`
}

function parseDragId(id: string): { col: ColumnKey; leadId: string } | null {
  const idx = id.indexOf("|")
  if (idx <= 0) return null
  return { col: id.slice(0, idx) as ColumnKey, leadId: id.slice(idx + 1) }
}

export default function PipelineClient({ initial }: { initial: Board | null }) {
  const [board, setBoard] = useState<Board | null>(initial)
  const [error, setError] = useState<string | null>(initial ? null : "No initial board.")
  const [loading, setLoading] = useState(false)
  const [activeCard, setActiveCard] = useState<BoardCard | null>(null)

  // 5px activation distance: a mousedown on the grip without movement
  // stays a click (so :active + the cursor-grab affordance still fire),
  // but any real drag intent kicks in immediately.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

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

  // Mirror every note/next-touch edit into board state so (a) the card
  // carries the latest text through an optimistic column move, and (b)
  // when a Card remounts (either via a move or a refresh), the NoteEditor
  // seeds from the fresh value instead of the stale initial card prop.
  const patchCard = useCallback(
    (leadId: string, patch: { note?: string | null; next_touch?: string | null }) => {
      setBoard((prev) => {
        if (!prev) return prev
        const cols: ColumnKey[] = ["new", "contacted", "replied", "booked"]
        const next: Board = { ...prev }
        for (const col of cols) {
          const idx = prev[col].findIndex((c) => c.lead_id === leadId)
          if (idx === -1) continue
          const updated = { ...prev[col][idx], ...patch }
          const list = prev[col].slice()
          list[idx] = updated
          next[col] = list
          break
        }
        return next
      })
    },
    [],
  )

  // Optimistic column move. Snapshots prior state so we can revert on
  // PATCH failure; `setStage` is the only backend hop.
  const moveCard = useCallback(
    async (leadId: string, from: ColumnKey, to: ColumnKey) => {
      if (from === to) return
      setBoard((prev) => {
        if (!prev) return prev
        const card = prev[from].find((c) => c.lead_id === leadId)
        if (!card) return prev
        return {
          ...prev,
          [from]: prev[from].filter((c) => c.lead_id !== leadId),
          [to]: [card, ...prev[to].filter((c) => c.lead_id !== leadId)],
        }
      })
      try {
        await setStage(leadId, to as Stage)
        setError(null)
      } catch (e) {
        setBoard((prev) => {
          if (!prev) return prev
          const card = prev[to].find((c) => c.lead_id === leadId)
          if (!card) return prev
          return {
            ...prev,
            [to]: prev[to].filter((c) => c.lead_id !== leadId),
            [from]: [card, ...prev[from].filter((c) => c.lead_id !== leadId)],
          }
        })
        setError(e instanceof Error ? e.message : "Could not move card.")
      }
    },
    [],
  )

  const handleDragStart = useCallback(
    (ev: DragStartEvent) => {
      const parsed = parseDragId(String(ev.active.id))
      if (!parsed || !board) {
        setActiveCard(null)
        return
      }
      const card = board[parsed.col].find((c) => c.lead_id === parsed.leadId) ?? null
      setActiveCard(card)
    },
    [board],
  )

  const handleDragEnd = useCallback(
    (ev: DragEndEvent) => {
      setActiveCard(null)
      if (!ev.over) return
      const from = parseDragId(String(ev.active.id))
      const to = String(ev.over.id) as ColumnKey
      if (!from) return
      void moveCard(from.leadId, from.col, to)
    },
    [moveCard],
  )

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

      <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd} onDragCancel={() => setActiveCard(null)}>
        {/* `items-stretch` + h-full on the droppable wrapper is what makes
            the drop target span the FULL column height, not just the
            rendered cards. In production the New column packs ~50 cards
            while Replied/Booked are often empty: a user dragging the 25th
            New card sideways would otherwise land in dead space below the
            short panel and the drop would silently no-op. Stretching the
            droppable to the tallest column fixes that. */}
        <div className="grid items-stretch gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((col) => (
            <Column
              key={col.key}
              columnKey={col.key}
              label={col.label}
              hint={col.hint}
              cards={board ? board[col.key] : []}
              loading={loading && !board}
              onCardEdit={patchCard}
            />
          ))}
        </div>

        {/* DragOverlay renders the card at the pointer during a drag so
            the actual column cell can collapse without visual jank. */}
        <DragOverlay>
          {activeCard ? (
            <div className="pointer-events-none w-72 rounded-sm border border-border bg-background p-3 shadow-lg opacity-95">
              <div className="flex items-start justify-between gap-2">
                <GripVertical className="size-4 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <h3 className="truncate font-semibold text-sm leading-tight">{activeCard.name}</h3>
                  <p className="truncate text-xs text-muted-foreground">
                    {[activeCard.city, activeCard.state].filter(Boolean).join(", ") || "—"}
                    {activeCard.mc ? ` · MC ${activeCard.mc}` : ""}
                  </p>
                </div>
              </div>
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
  )
}

function Column({
  columnKey,
  label,
  hint,
  cards,
  loading,
  onCardEdit,
}: {
  columnKey: ColumnKey
  label: string
  hint: string
  cards: BoardCard[]
  loading: boolean
  onCardEdit: (leadId: string, patch: { note?: string | null; next_touch?: string | null }) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: columnKey })

  return (
    <div
      ref={setNodeRef}
      className={`flex h-full min-h-[32rem] flex-col rounded-sm ${
        isOver ? "ring-2 ring-safety/60" : ""
      }`}
    >
      <Panel
        title={`${label} (${cards.length})`}
        description={hint}
        className="flex-1"
        bodyClassName="flex flex-1 flex-col gap-3"
      >
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : cards.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nothing here yet.</p>
        ) : (
          cards.map((card) => (
            <Card
              key={card.lead_id}
              card={card}
              fromColumn={columnKey}
              onCardEdit={onCardEdit}
            />
          ))
        )}
      </Panel>
    </div>
  )
}

function Card({
  card,
  fromColumn,
  onCardEdit,
}: {
  card: BoardCard
  fromColumn: ColumnKey
  onCardEdit: (leadId: string, patch: { note?: string | null; next_touch?: string | null }) => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: makeDragId(fromColumn, card.lead_id),
  })

  return (
    <article
      ref={setNodeRef}
      className={`flex flex-col gap-2 rounded-sm border border-border bg-background p-3 ${
        isDragging ? "opacity-30" : ""
      }`}
    >
      <header className="flex items-start justify-between gap-2">
        {/* Only the grip is the handle — spread listeners here, not on the
            article — so the textarea + date input remain text-selectable. */}
        <div
          {...listeners}
          {...attributes}
          className="shrink-0 cursor-grab touch-none text-muted-foreground hover:text-foreground active:cursor-grabbing"
          aria-label="Drag card between columns"
          title="Drag between columns"
        >
          <GripVertical className="size-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
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

      <NoteEditor card={card} onCardEdit={onCardEdit} />

      <footer className="mt-1 flex gap-2 text-xs">
        <Link
          href={`/call-list?lead=${encodeURIComponent(card.lead_id)}`}
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

function NoteEditor({
  card,
  onCardEdit,
}: {
  card: BoardCard
  onCardEdit: (leadId: string, patch: { note?: string | null; next_touch?: string | null }) => void
}) {
  // Seed from card.* once; after that, every keystroke mirrors itself
  // back into board state via onCardEdit, so a drop-and-move carries the
  // latest text (including debounce-pending edits) through the remount.
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
          onCardEdit(card.lead_id, { note: ev.target.value })
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
              onCardEdit(card.lead_id, { next_touch: ev.target.value || null })
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
