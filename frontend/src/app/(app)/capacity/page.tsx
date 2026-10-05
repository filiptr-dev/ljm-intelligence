"use client"

/**
 * Capacity Posts — /capacity
 *
 * Two post kinds: empty TRUCK and freight LOAD, both persisted server-side.
 * Below the form, a live list of the posts + a "Suggested recipients" panel that
 * ranks crawled brokers/shippers for whichever post the operator picks. Send is
 * simulated for the demo — the "Draft email" button opens /emails/compose,
 * the single-recipient composer (campaign builder is only for many recipients).
 */

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"
import { Sparkles, Truck, Package } from "lucide-react"
import { PageHeader, Panel, RegionTag } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Segmented } from "@/components/app/segmented"
import { ScoreChip } from "@/components/app/live-feed"
import { api } from "@/lib/api/client"

type PostOut = {
  id: string
  kind: "truck" | "load"
  equipment: string
  origin_city: string | null
  origin_state: string
  destinations: string[]
  available_date: string | null
  dest_city: string | null
  dest_state: string | null
  pickup_date: string | null
  weight_lbs: number | null
  rate_usd: number | null
  notes: string | null
  status: string
  created_at: string
}

type Suggestion = {
  lead_id: string
  name: string
  state: string
  email: string | null
  phone: string | null
  score: number
  reason: string
}

type MatchedShipper = {
  candidate_id: string
  name: string
  state: string
  city: string | null
  primary_email: string | null
  phone: string | null
  score: number
  reason: string
  promoted_lead_id: string | null
}

// Build the /emails/compose prefill URL. Subject + body are plain template
// literals (no backend templating) and all three params are URL-encoded.
// Body is capped at 1500 chars to keep the URL under common limits.
function buildDraftEmailHref(post: PostOut, s: MatchedShipper): string {
  const to = s.primary_email ?? ""
  let subject: string
  let body: string
  if (post.kind === "truck") {
    const originLoc = post.origin_city || post.origin_state
    const avail = post.available_date ? ` ${post.available_date}` : ""
    subject = `Capacity available: ${post.equipment} out of ${originLoc}${avail}`
    const destLine = post.destinations.length
      ? `heading toward ${post.destinations.join(", ")}`
      : "available for your next lane"
    body = [
      `Hi ${s.name} team,`,
      ``,
      `We have a ${post.equipment} empty out of ${originLoc}${post.available_date ? ` on ${post.available_date}` : ""}, ${destLine}.`,
      `Saw you ship out of ${s.state}${s.city ? ` (${s.city})` : ""} — figured we'd put it in front of you first.`,
      ``,
      `Reply here or call us if the lane works — happy to send specs and rates.`,
      ``,
      `— LJM International`,
    ].join("\n")
  } else {
    const originLoc = post.origin_city || post.origin_state
    const destLoc = post.dest_city || post.dest_state || ""
    const pickup = post.pickup_date ? ` ${post.pickup_date}` : ""
    subject = `Looking for a home for a ${post.equipment} load: ${post.origin_state} → ${post.dest_state ?? "?"}${pickup}`
    body = [
      `Hi ${s.name} team,`,
      ``,
      `We're moving a ${post.equipment} load from ${originLoc} to ${destLoc}${post.pickup_date ? ` picking up ${post.pickup_date}` : ""}${post.weight_lbs ? ` (${post.weight_lbs} lbs)` : ""}.`,
      `You're on our ${s.state} drop list — want to see if the lane lines up.`,
      ``,
      `Reply here or give us a call if it's a fit.`,
      ``,
      `— LJM International`,
    ].join("\n")
  }
  if (body.length > 1500) body = body.slice(0, 1500)
  const params = new URLSearchParams()
  if (to) params.set("to", to)
  params.set("subject", subject)
  params.set("body", body)
  return `/emails/compose?${params.toString()}`
}

const EQUIPMENT = ["Dry Van", "Reefer", "Flatbed", "Step Deck", "Power Only"]

export default function CapacityPage() {
  // Overview's "New post" button links here with ?new=1 — scroll the New
  // post panel into view so operators coming from the Today desk land on
  // the form, not the top of the list.
  React.useEffect(() => {
    if (typeof window === "undefined") return
    const params = new URLSearchParams(window.location.search)
    if (params.get("new") === "1") {
      const el = document.getElementById("new-post")
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" })
    }
  }, [])

  const [kind, setKind] = React.useState<"truck" | "load">("truck")
  const [equipment, setEquipment] = React.useState("Dry Van")
  const [originCity, setOriginCity] = React.useState("")
  const [originState, setOriginState] = React.useState("NJ")
  const [destinations, setDestinations] = React.useState("PA, NY, GA")
  const [availableDate, setAvailableDate] = React.useState("")
  const [destCity, setDestCity] = React.useState("")
  const [destState, setDestState] = React.useState("GA")
  const [pickupDate, setPickupDate] = React.useState("")
  const [weight, setWeight] = React.useState("")
  const [rate, setRate] = React.useState("")
  const [notes, setNotes] = React.useState("")
  const [submitting, setSubmitting] = React.useState(false)

  const [posts, setPosts] = React.useState<PostOut[]>([])
  const [selected, setSelected] = React.useState<PostOut | null>(null)
  const [suggestions, setSuggestions] = React.useState<Suggestion[]>([])
  const [shippers, setShippers] = React.useState<MatchedShipper[]>([])
  const [loadingSuggestions, setLoadingSuggestions] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      const { data } = await api.GET("/capacity/posts", { params: { query: { limit: 50 } } })
      setPosts(((data as { items?: PostOut[] } | undefined)?.items) ?? [])
    } catch {
      /* keep whatever we had */
    }
  }, [])

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const submit = async () => {
    setSubmitting(true)
    try {
      const body =
        kind === "truck"
          ? {
              kind,
              equipment,
              origin_city: originCity || undefined,
              origin_state: originState,
              destinations: destinations.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
              available_date: availableDate || undefined,
            }
          : {
              kind,
              equipment,
              origin_city: originCity || undefined,
              origin_state: originState,
              dest_city: destCity || undefined,
              dest_state: destState,
              pickup_date: pickupDate || undefined,
              weight_lbs: weight ? Number(weight) : undefined,
              rate_usd: rate ? Number(rate) : undefined,
              notes: notes || undefined,
            }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { data, response } = await api.POST("/capacity/posts", { body: body as any })
      if (!response.ok || !data) throw new Error(`create ${response.status}`)
      const created = data as unknown as PostOut
      toast.success(`${kind === "truck" ? "Truck" : "Load"} post created`, { description: `${created.origin_state} — ${created.equipment}` })
      await load()
      setSelected(created)
    } catch (e) {
      toast.error("Couldn't create post", { description: String(e) })
    } finally {
      setSubmitting(false)
    }
  }

  React.useEffect(() => {
    if (!selected) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSuggestions([])
      setShippers([])
      return
    }
    let cancelled = false
    ;(async () => {
      setLoadingSuggestions(true)
      try {
        const { data } = await api.GET("/capacity/posts/{post_id}/suggestions", {
          params: { path: { post_id: selected.id }, query: { top: 20 } },
        })
        if (!cancelled) {
          const payload = data as { items?: Suggestion[]; shippers?: MatchedShipper[] } | undefined
          setSuggestions(payload?.items ?? [])
          setShippers(payload?.shippers ?? [])
        }
      } finally {
        if (!cancelled) setLoadingSuggestions(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selected])

  // Lane states for the empty-state copy (origin + truck destinations + load drop).
  const laneStates = React.useMemo(() => {
    if (!selected) return [] as string[]
    const set = new Set<string>()
    if (selected.origin_state) set.add(selected.origin_state)
    for (const d of selected.destinations ?? []) if (d) set.add(d)
    if (selected.dest_state) set.add(selected.dest_state)
    return Array.from(set)
  }, [selected])

  return (
    <>
      <PageHeader
        eyebrow="Capacity Posts"
        title="Post trucks and loads"
        description="Post empty trucks or the freight you're carrying — the crawler ranks brokers to send it to, so nothing goes to dead inboxes."
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,460px)]">
        <div className="space-y-5">
          <Panel id="new-post" title="New post" description="Store the post — the ranked broker list appears on the right the moment it's saved.">
            <div className="mb-4">
              <Segmented
                value={kind}
                onChange={(v) => setKind(v as "truck" | "load")}
                options={[
                  { value: "truck", label: <span className="inline-flex items-center gap-1.5"><Truck className="size-4" /> Empty truck</span> },
                  { value: "load", label: <span className="inline-flex items-center gap-1.5"><Package className="size-4" /> Load / freight</span> },
                ]}
              />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Equipment</Label>
                <Select value={equipment} onValueChange={(v) => v && setEquipment(v)}>
                  <SelectTrigger><SelectValue>{equipment}</SelectValue></SelectTrigger>
                  <SelectContent>{EQUIPMENT.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Origin state</Label>
                <Input value={originState} onChange={(e) => setOriginState(e.target.value.toUpperCase().slice(0, 2))} maxLength={2} />
              </div>
              <div className="space-y-1.5">
                <Label>Origin city</Label>
                <Input value={originCity} onChange={(e) => setOriginCity(e.target.value)} placeholder="Lincoln Park" />
              </div>
              {kind === "truck" ? (
                <>
                  <div className="space-y-1.5">
                    <Label>Available date</Label>
                    <Input type="date" value={availableDate} onChange={(e) => setAvailableDate(e.target.value)} />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label>Preferred destination states (comma-separated)</Label>
                    <Input value={destinations} onChange={(e) => setDestinations(e.target.value)} placeholder="PA, NY, GA" />
                  </div>
                </>
              ) : (
                <>
                  <div className="space-y-1.5">
                    <Label>Destination state</Label>
                    <Input value={destState} onChange={(e) => setDestState(e.target.value.toUpperCase().slice(0, 2))} maxLength={2} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Destination city</Label>
                    <Input value={destCity} onChange={(e) => setDestCity(e.target.value)} placeholder="Atlanta" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Pickup date</Label>
                    <Input type="date" value={pickupDate} onChange={(e) => setPickupDate(e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Weight (lbs)</Label>
                    <Input type="number" inputMode="numeric" value={weight} onChange={(e) => setWeight(e.target.value)} placeholder="42000" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Rate ($)</Label>
                    <Input type="number" inputMode="numeric" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="2400" />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label>Notes</Label>
                    <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} placeholder="Palletised, dock-height, driver assist not required" />
                  </div>
                </>
              )}
            </div>

            <div className="mt-4 flex items-center justify-end">
              <Button onClick={submit} disabled={submitting} className="font-semibold">
                {submitting ? "Saving…" : `Post ${kind === "truck" ? "truck" : "load"}`}
              </Button>
            </div>
          </Panel>

          <Panel title="Posts" description={`${posts.length} on file. Click one to see suggested recipients.`}>
            {posts.length ? (
              <ul className="space-y-2">
                {posts.map((p) => {
                  const isSelected = selected?.id === p.id
                  return (
                    <li key={p.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(p)}
                        className={`flex w-full items-start gap-3 rounded-sm border px-3 py-2 text-left text-sm transition hover:bg-muted ${isSelected ? "border-asphalt bg-accent" : "border-border"}`}
                      >
                        {p.kind === "truck" ? <Truck className="mt-0.5 size-4 text-chart-2" /> : <Package className="mt-0.5 size-4 text-safety" />}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 font-semibold">
                            <span className="text-[0.68rem] font-bold tracking-wider text-muted-foreground uppercase">{p.kind}</span>
                            <span className="truncate">{p.equipment}</span>
                            <span className="ml-auto font-mono text-xs text-muted-foreground">{new Date(p.created_at).toLocaleDateString()}</span>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {p.kind === "truck"
                              ? `${p.origin_city ? p.origin_city + ", " : ""}${p.origin_state} · avail ${p.available_date ?? "—"} · to ${p.destinations.join(", ") || "any"}`
                              : `${p.origin_city ?? ""} ${p.origin_state} → ${p.dest_city ?? ""} ${p.dest_state ?? ""} · ${p.pickup_date ?? ""}${p.rate_usd ? ` · $${p.rate_usd}` : ""}${p.weight_lbs ? ` · ${p.weight_lbs} lbs` : ""}`}
                          </div>
                        </div>
                      </button>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No posts yet. Save one above to see suggested recipients.</p>
            )}
          </Panel>
        </div>

        <div className="space-y-4 xl:sticky xl:top-20 xl:self-start">
          <Panel
            title={<span className="inline-flex items-center gap-2"><Sparkles className="size-4 text-chart-2" /> Suggested recipients</span>}
            description={selected ? `Ranked by fit for post ${selected.id.slice(-6)}` : "Pick a post to see who to send it to."}
          >
            {!selected ? (
              <p className="text-sm text-muted-foreground">Pick a post from the list to see up to 20 brokers/shippers ranked by fit.</p>
            ) : loadingSuggestions ? (
              <p className="text-sm text-muted-foreground">Ranking crawled brokers…</p>
            ) : suggestions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No matches — try a different origin state, or run the crawler to bring in fresh leads.</p>
            ) : (
              <ul className="max-h-[520px] space-y-2 overflow-y-auto">
                {suggestions.map((s) => (
                  <li key={s.lead_id} className="rounded-sm border border-border p-2.5">
                    <div className="flex items-start gap-2">
                      <RegionTag region="US" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-semibold">{s.name}</span>
                          <span className="text-xs text-muted-foreground">{s.state}</span>
                          <span className="ml-auto"><ScoreChip score={s.score} /></span>
                        </div>
                        <div className="text-xs text-muted-foreground">{s.reason}</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
                          {s.email ? <span className="font-mono">{s.email}</span> : <span className="text-warn">no email</span>}
                          {s.phone ? <span className="text-muted-foreground">{s.phone}</span> : null}
                          <Link
                            href={`/emails/compose?lead=${encodeURIComponent(s.lead_id)}`}
                            className="ml-auto rounded-sm border border-border px-2 py-0.5 text-[0.7rem] font-semibold hover:bg-muted"
                          >
                            Draft email
                          </Link>
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel
            title={<span className="inline-flex items-center gap-2"><Sparkles className="size-4 text-safety" /> Matched shippers</span>}
            description={selected ? `Direct shippers on this lane (from Shipper Finder)` : "Pick a post to see direct shippers on this lane."}
          >
            {!selected ? (
              <p className="text-sm text-muted-foreground">Pick a post from the list to see matched shippers.</p>
            ) : loadingSuggestions ? (
              <p className="text-sm text-muted-foreground">Finding shippers on this lane…</p>
            ) : shippers.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No shippers on this lane yet — run the Shipper Finder for {laneStates.join(", ") || "these states"} or widen your destinations.
              </p>
            ) : (
              <ul className="max-h-[520px] space-y-2 overflow-y-auto">
                {shippers.map((s) => (
                  <li key={s.candidate_id} className="rounded-sm border border-border p-2.5">
                    <div className="flex items-start gap-2">
                      <RegionTag region="US" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-semibold">{s.name}</span>
                          <span className="text-xs text-muted-foreground">{s.city ? `${s.city}, ${s.state}` : s.state}</span>
                          <span className="ml-auto"><ScoreChip score={s.score} /></span>
                        </div>
                        <div className="text-xs text-muted-foreground">{s.reason}</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
                          {s.primary_email ? <span className="font-mono">{s.primary_email}</span> : <span className="text-warn">no email</span>}
                          {s.phone ? <span className="text-muted-foreground">{s.phone}</span> : null}
                          <Link
                            href={buildDraftEmailHref(selected, s)}
                            className="ml-auto rounded-sm border border-border px-2 py-0.5 text-[0.7rem] font-semibold hover:bg-muted"
                          >
                            Draft email
                          </Link>
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  )
}
