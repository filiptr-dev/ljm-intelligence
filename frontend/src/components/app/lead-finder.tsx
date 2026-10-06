"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { BadgeCheck, Mail, MapPin, RefreshCw, Search, Send, Sparkles, Zap } from "lucide-react"
import { useBackendLeads } from "@/lib/backend-leads"
import { useJobStatus, useLatestRun } from "@/lib/use-backend"
import { Plate } from "@/components/brand/marks"
import { Tire } from "@/components/brand/tire"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { explainScore, type LookalikeProfile } from "@/lib/analytics/similarity"
import { SOURCE_LIST } from "@/lib/data/leads"
import { EQUIPMENT, LEAD_KIND_LABEL, type Lead, type LeadKind } from "@/lib/data/types"
import type { Region } from "@/lib/data/geo"
import { num, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import { DEFAULT_DESIGN, toRecipient, useEngine } from "./engine"
import { LiveFeed, ScoreChip } from "./live-feed"
import { Panel, RegionTag } from "./ui"
import { Segmented } from "./segmented"
import { useAutoOutreach } from "./use-auto-outreach"

/** One-click templates: brokers and forwarders get a capacity pitch, shippers a direct-carrier pitch. */
const QUICK: Record<"capacity" | "shipper", { type: string; subject: string; body: string }> = {
  capacity: {
    type: "new_leads",
    subject: "{{equipment}} capacity for {{company}}",
    body: "Hi {{first_name}},\n\nI'm reaching out from LJM International, a dry-van carrier based in Lincoln Park, NJ, running the eastern US. We run {{equipment}} daily on {{lane}}, which is right in your network.\n\nWe answer quotes within 15 minutes and track every load live. Anything we can cover this week?\n\nBest regards,\n{{sender}}",
  },
  shipper: {
    type: "shipper_direct",
    subject: "Direct {{equipment}} trucks for {{company}}, no broker in between",
    body: "Hi {{first_name}},\n\nI'm reaching out from LJM International, a dry-van carrier based in Lincoln Park, NJ. We run {{equipment}} on {{lane}} every week across the eastern US.\n\nWorking with us directly means our own drivers, live tracking on every load and no broker margin on top of the rate. Happy to start with one trial load.\n\nBest regards,\n{{sender}}",
  },
}

export function LeadFinder({ pool, profile }: { pool: Lead[]; profile: LookalikeProfile }) {
  const router = useRouter()
  const { contacted, sendCampaign } = useEngine()
  const [autoOutreach, setAutoOutreach] = useAutoOutreach()
  const [q, setQ] = React.useState("")
  const [minScore, setMinScore] = React.useState(0)
  const [equipment, setEquipment] = React.useState("all")
  const [kind, setKind] = React.useState<"all" | LeadKind>("all")
  const [hideContacted, setHideContacted] = React.useState(false)
  const [selected, setSelected] = React.useState<Set<string>>(new Set())
  const [limit, setLimit] = React.useState(60)
  const [open, setOpen] = React.useState<Lead | null>(null)
  const [confirm, setConfirm] = React.useState(false)
  const { real: realLeads, live: backendLive, reload: reloadRealLeads } = useBackendLeads(200)
  const { run: latestRun, running: triggering, trigger: triggerCrawl } = useLatestRun()
  // MF3 — poll the procrastinate job directly so a worker-side failure
  // surfaces as a toast + a cleared "Crawling…" state, instead of the
  // button spinning forever while CrawlRun stays queued. The CrawlRun
  // row polling (useLatestRun) still drives the primary live state; this
  // hook is the escape hatch for failures that never reach the row.
  const [crawlJobId, setCrawlJobId] = React.useState<number | null>(null)
  useJobStatus(crawlJobId, {
    onDone: () => {
      setCrawlJobId(null)
      void reloadRealLeads()
    },
    onFail: (err) => {
      setCrawlJobId(null)
      toast.error("Crawl failed", { description: err ?? "worker error" })
    },
  })

  // Fake per-session `liveLeads` are gone (plan 2026-10-06): the table shows
  // only real backend leads + the design-pool fallback.
  const all = React.useMemo(() => [...realLeads, ...pool], [realLeads, pool])
  const filtered = React.useMemo(
    () =>
      all.filter(
        (l) =>
          (kind === "all" || l.kind === kind) &&
          l.score >= minScore &&
          (equipment === "all" || l.equipment.includes(equipment as Lead["equipment"][number])) &&
          (!hideContacted || !contacted.has(l.id)) &&
          (!q || leadHaystack(l).includes(q.toLowerCase())),
      ),
    [all, kind, minScore, equipment, hideContacted, contacted, q],
  )
  const quick = filtered.filter((l) => l.score >= 75 && l.emailVerified && !contacted.has(l.id))

  const bySource = React.useMemo(() => {
    const m = new Map<string, number>()
    all.forEach((l) => m.set(`${l.region}:${l.source}`, (m.get(`${l.region}:${l.source}`) ?? 0) + 1))
    return m
  }, [all])
  // The old "which source is scanning right now" highlight was driven by the
  // fake feed's `scan` events. The real backend never emits a `scan` kind, so
  // the highlight is dropped (plan 2026-10-06: removed, not faked).
  const scanning: string | undefined = undefined

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const sendQuick = (leads: Lead[], name: string) => {
    const shippers = leads.filter((l) => l.kind === "Shipper")
    const others = leads.filter((l) => l.kind !== "Shipper")
    for (const [group, tpl, suffix] of [[others, QUICK.capacity, ""], [shippers, QUICK.shipper, " · shippers"]] as const) {
      if (!group.length) continue
      sendCampaign({
        name: shippers.length && others.length ? `${name}${suffix || " · brokers & forwarders"}` : name,
        type: tpl.type,
        subject: tpl.subject,
        body: tpl.body,
        design: DEFAULT_DESIGN,
        recipients: group.map(toRecipient),
      })
    }
    toast.success(`${leads.length} personalised email${leads.length > 1 ? "s" : ""} queued`, {
      description: "Delivery, opens and AI-read replies are tracked in Campaigns.",
      action: { label: "View", onClick: () => router.push("/campaigns") },
    })
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0 space-y-5">
        <BackendStatusBar
          live={backendLive}
          realCount={realLeads.length}
          latest={latestRun}
          triggering={triggering}
          onCrawl={async () => {
            const jid = await triggerCrawl()
            if (jid != null) setCrawlJobId(jid)
            // Refresh the real-leads panel after a short wait so the freshly-persisted rows show up.
            setTimeout(reloadRealLeads, 4000)
          }}
        />
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-border bg-border sm:grid-cols-4 2xl:grid-cols-7">
          {(["US"] as Region[]).flatMap((r) => SOURCE_LIST[r].map((src) => ({ r, src }))).map(({ r, src }) => {
            const active = scanning === `${r}:${src}`
            return (
              <div key={`${r}:${src}`} className={cn("flex items-center gap-2.5 bg-card px-3 py-2.5", active && "bg-accent")}>
                <Tire spinning={active} className="size-6 shrink-0" />
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-sm font-semibold">{src}</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <RegionTag region={r} /> {num(bySource.get(`${r}:${src}`) ?? 0)} leads
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        <div className="rounded-sm border border-border bg-card">
          <div className="flex flex-wrap items-center gap-3 border-b border-border p-3">
            <div className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search company, city, lane, industry…" className="pl-8" />
            </div>
            <Segmented
              value={kind}
              onChange={setKind}
              options={[{ value: "all", label: "Everyone" }, { value: "Broker", label: "Brokers" }, { value: "Shipper", label: "Shippers" }, { value: "Forwarder", label: "Forwarders / 3PL" }]}
            />
            <Select value={equipment} onValueChange={(v) => setEquipment(v ?? "all")}>
              <SelectTrigger className="w-40"><SelectValue>{equipment === "all" ? "All equipment" : equipment}</SelectValue></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All equipment</SelectItem>
                {EQUIPMENT.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}
              </SelectContent>
            </Select>
            <div className="flex w-48 items-center gap-3">
              <span className="text-xs whitespace-nowrap text-muted-foreground">Match ≥ <b className="font-mono text-foreground">{minScore}</b></span>
              <Slider value={[minScore]} onValueChange={(v) => setMinScore(Array.isArray(v) ? v[0] : v)} max={95} step={5} />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={hideContacted} onCheckedChange={setHideContacted} /> Hide contacted
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/50 px-3 py-2.5">
            <span className="text-sm">
              <b className="num font-mono">{num(filtered.length)}</b> leads
            </span>
            <div className="ml-auto flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!selected.size}
                onClick={() => router.push(`/outreach?audience=new&ids=${[...selected].join(",")}&campaign=new_leads`)}
              >
                Customise &amp; send {selected.size || ""}
              </Button>
              <Button className="font-semibold" disabled={!quick.length} onClick={() => setConfirm(true)}>
                <Zap /> One-click outreach · {quick.length} high matches
              </Button>
            </div>
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10 pl-4">
                  <Checkbox
                    checked={filtered.length > 0 && filtered.slice(0, limit).every((l) => selected.has(l.id))}
                    onCheckedChange={(v) => setSelected(v ? new Set(filtered.slice(0, limit).map((l) => l.id)) : new Set())}
                    aria-label="Select all"
                  />
                </TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Match</TableHead>
                <TableHead>Lanes</TableHead>
                <TableHead>Equipment</TableHead>
                <TableHead>Found</TableHead>
                <TableHead className="pr-4 text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.slice(0, limit).map((l) => {
                // "NEW" badge used to light up for fake per-session liveLeads;
                // the real "found today" surface is the top-bar `found` counter.
                const isNew = false
                const done = contacted.has(l.id)
                return (
                  <TableRow key={l.id} className={cn("cursor-pointer", isNew && "animate-feed bg-accent/60")} onClick={() => setOpen(l)}>
                    <TableCell className="pl-4" onClick={(e) => e.stopPropagation()}>
                      <Checkbox checked={selected.has(l.id)} onCheckedChange={() => toggle(l.id)} aria-label={`Select ${l.name}`} />
                    </TableCell>
                    <TableCell className="max-w-[260px]">
                      <div className="flex items-center gap-2">
                        <RegionTag region={l.region} />
                        <span className="truncate font-semibold">{l.name}</span>
                        {isNew ? <span className="rounded-[3px] bg-safety px-1 text-[0.6rem] font-bold text-asphalt">NEW</span> : null}
                      </div>
                      <div className="mt-0.5 truncate pl-8 text-xs text-muted-foreground">{l.hq} · {l.size} · ~{num(l.monthlyLoads)} loads/mo</div>
                    </TableCell>
                    <TableCell><KindBadge kind={l.kind} industry={l.industry} /></TableCell>
                    <TableCell><ScoreChip score={l.score} /></TableCell>
                    <TableCell className="max-w-[180px] truncate text-xs">
                      {l.lanes[0].origin} → {l.lanes[0].destination}
                      {l.lanes.length > 1 ? <span className="text-muted-foreground"> +{l.lanes.length - 1}</span> : null}
                    </TableCell>
                    <TableCell className="max-w-[120px] truncate text-xs">{l.equipment.join(", ")}</TableCell>
                    <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                      <div suppressHydrationWarning>{timeAgo(l.discoveredAt)}</div>
                      <div className="text-[0.7rem]">{l.source}</div>
                    </TableCell>
                    <TableCell className="pr-4 text-right" onClick={(e) => e.stopPropagation()}>
                      {done ? (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-good"><BadgeCheck className="size-3.5" /> Contacted</span>
                      ) : (
                        <Button size="xs" variant="outline" onClick={() => sendQuick([l], `Quick send · ${l.name}`)}>
                          <Send /> Send
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          {filtered.length > limit ? (
            <div className="border-t border-border p-3 text-center">
              <Button variant="ghost" onClick={() => setLimit((x) => x + 60)}>Show more ({filtered.length - limit} left)</Button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="space-y-5 xl:sticky xl:top-20 xl:self-start">
        <Panel title="Auto-outreach" description="Email new high-match leads as soon as they're verified">
          <label className="flex items-center justify-between gap-3">
            <span className="text-sm">Auto-send when match ≥ 70</span>
            <Switch checked={autoOutreach} onCheckedChange={(v) => { void setAutoOutreach(v) }} />
          </label>
          <p className="mt-2 text-xs text-muted-foreground">Emails go out in your peak reply windows, with the “Capacity intro” template personalised by the AI.</p>
        </Panel>
        <Panel title="Crawler activity" bodyClassName="max-h-[560px] overflow-y-auto py-0">
          <LiveFeed limit={25} />
        </Panel>
      </div>

      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-display text-xl">Send to {quick.length} high-match companies?</DialogTitle>
            <DialogDescription>
              Brokers and forwarders get a “capacity intro”, direct shippers get a “direct carrier, no broker” email. Each one is personalised with their lanes and equipment. Only verified addresses that haven&apos;t been contacted are included.
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-48 space-y-1 overflow-y-auto rounded-sm border border-border p-2 text-sm">
            {quick.slice(0, 40).map((l) => (
              <li key={l.id} className="flex items-center gap-2">
                <ScoreChip score={l.score} /> <span className="truncate">{l.name}</span> <KindBadge kind={l.kind} />
                <span className="ml-auto truncate text-xs text-muted-foreground">{l.contact.email}</span>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button
              className="font-semibold"
              onClick={() => {
                sendQuick(quick, `High-match leads · ${new Date().toLocaleDateString("en-US", { month: "short", day: "numeric" })}`)
                setConfirm(false)
              }}
            >
              <Send /> Send {quick.length} emails
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-md">
          {open ? (
            <LeadDetail
              lead={open}
              profile={profile}
              contacted={contacted.has(open.id)}
              onSend={() => sendQuick([open], `Quick send · ${open.name}`)}
              onDraftAI={() => router.push(`/emails/compose?lead=${open.id}`)}
            />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function LeadDetail({ lead, profile, contacted, onSend, onDraftAI }: { lead: Lead; profile: LookalikeProfile; contacted: boolean; onSend: () => void; onDraftAI: () => void }) {
  const why = explainScore(lead, profile)
  return (
    <>
      <SheetHeader className="border-b border-border">
        <div className="flex items-center gap-2">
          <RegionTag region={lead.region} />
          <Plate region={lead.region} value={lead.registration.replace(/^[A-Z]{2}(?=\d)/, "")} country={lead.country} />
        </div>
        <SheetTitle className="font-display text-2xl leading-tight">{lead.name}</SheetTitle>
        <SheetDescription className="flex items-center gap-1"><MapPin className="size-3.5" /> {lead.hq} · {lead.size} · ~{num(lead.monthlyLoads)} {lead.kind === "Shipper" ? "truckloads shipped" : "loads"} / month</SheetDescription>
        <div><KindBadge kind={lead.kind} industry={lead.industry} /></div>
      </SheetHeader>
      <div className="space-y-5 p-4">
        <div className="flex items-center gap-4 rounded-sm bg-asphalt p-4 text-white">
          <div className="text-center">
            <div className="text-4xl font-semibold">{lead.score}</div>
            <div className="text-[0.65rem] tracking-wider text-[#8b9098] uppercase">Match</div>
          </div>
          <div className="flex-1 space-y-2 text-xs">
            {([["Lane footprint", why.footprint], ["Equipment", why.equipment], ["Company size", why.size]] as const).map(([k, v]) => (
              <div key={k}>
                <div className="mb-0.5 flex justify-between"><span className="text-[#b9bcc2]">{k}</span><span className="font-mono">{v}</span></div>
                <div className="h-1.5 bg-[#2c2e33]"><div className="h-full bg-safety" style={{ width: `${v}%` }} /></div>
              </div>
            ))}
          </div>
        </div>
        <p className="flex gap-2 text-sm">
          <Sparkles className="mt-0.5 size-4 shrink-0 text-chart-2" />
          <span>
            {lead.kind === "Shipper" && lead.score >= 45
              ? `A direct shipper${lead.industry ? ` in ${lead.industry.toLowerCase()}` : ""} whose freight moves on lanes and trailers you already run. Selling direct means no broker margin, so prioritise.`
              : lead.score >= 70
              ? "Very close to your core partners: they run the same regions and trailers your trucks already cover. Prioritise."
              : lead.score >= 45
                ? "Partial fit: some lanes overlap with your network. Worth a capacity email."
                : "Low fit: their lanes are mostly outside your operating area."}
          </span>
        </p>
        <div>
          <div className="eyebrow mb-1.5">Lanes</div>
          <ul className="space-y-1 text-sm">
            {lead.lanes.map((l, i) => <li key={i} className="font-mono text-[0.8rem]">{l.origin} → {l.destination}</li>)}
          </ul>
        </div>
        <div>
          <div className="eyebrow mb-1.5">Equipment</div>
          <div className="flex flex-wrap gap-1.5">{lead.equipment.map((e) => <span key={e} className="rounded-sm bg-muted px-2 py-0.5 text-sm">{e}</span>)}</div>
        </div>
        <div className="rounded-sm border border-border p-3 text-sm">
          <div className="eyebrow mb-1.5">Contact</div>
          <div className="font-semibold">{lead.contact.name}</div>
          <div className="text-muted-foreground">{lead.contact.title}</div>
          <div className="mt-1 flex items-center gap-1.5"><Mail className="size-3.5" /> {lead.contact.email} {lead.emailVerified ? <BadgeCheck className="size-3.5 text-good" /> : <span className="text-xs text-warn">unverified</span>}</div>
          <div className="mt-0.5 text-muted-foreground">{lead.contact.phone}</div>
        </div>
        <div className="text-xs text-muted-foreground">Found via {lead.source} · {timeAgo(lead.discoveredAt)}</div>
        <div className="flex flex-wrap gap-2">
          <Button className="flex-1 min-h-11 font-semibold" onClick={onDraftAI}>
            <Sparkles /> Draft with AI
          </Button>
          <Button variant="outline" className="min-h-11" disabled={contacted} onClick={onSend}>
            <Send /> {contacted ? "Already contacted" : "Quick send"}
          </Button>
          <Link href={`/emails/compose?lead=${lead.id}`} className="inline-flex h-9 items-center rounded-lg border border-border px-3 text-sm hover:bg-muted">Customise</Link>
        </div>
      </div>
    </>
  )
}

/**
 * Live/Simulated status bar + Crawl-now button.
 *
 * Honesty rule: when the backend is unreachable we say "Simulated", not "Live".
 * `backendLive === null` means the first health poll hasn't come back yet, so
 * we show a neutral "Checking…" pill instead of guessing.
 */
function BackendStatusBar({
  live,
  realCount,
  latest,
  triggering,
  onCrawl,
}: {
  live: boolean | null
  realCount: number
  latest: import("@/lib/use-backend").LatestRun
  triggering: boolean
  onCrawl: () => Promise<void>
}) {
  const active = latest?.status === "running" || latest?.status === "queued"
  const badge =
    live === null
      ? { label: "Checking backend…", cls: "bg-muted text-muted-foreground" }
      : live
        ? { label: "LIVE", cls: "bg-good text-white" }
        : { label: "SIMULATED", cls: "bg-warn text-asphalt" }
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-sm border border-border bg-card px-3 py-2.5">
      <span className={cn("inline-flex items-center rounded-[3px] px-2 py-0.5 text-[0.68rem] font-bold tracking-wider", badge.cls)}>{badge.label}</span>
      <span className="text-sm">
        {live
          ? <>Real crawler feed · <b className="num font-mono">{num(realCount)}</b> leads from the backend</>
          : "Backend unreachable — showing the simulated feed. Real crawler data will appear once it wakes."}
      </span>
      {active ? (
        <span className="ml-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <RefreshCw className="size-3.5 animate-spin" />
          Crawl {latest?.status}…
          {latest?.counts?.discovered ? ` · ${latest.counts.discovered} discovered` : ""}
        </span>
      ) : latest?.finished_at ? (
        <span className="ml-2 text-xs text-muted-foreground">
          last run: {timeAgo(latest.finished_at)} · new {latest.counts?.new ?? 0} · scored {latest.counts?.scored ?? 0}
        </span>
      ) : null}
      <div className="ml-auto">
        <Button
          size="sm"
          variant={live ? "default" : "outline"}
          disabled={triggering || active || !live}
          onClick={() => { void onCrawl() }}
          className="font-semibold"
        >
          <RefreshCw className={cn("size-3.5", (triggering || active) && "animate-spin")} />
          {active ? "Crawling…" : triggering ? "Starting…" : "Crawl now"}
        </Button>
      </div>
    </div>
  )
}

export const leadHaystack = (l: Lead) =>
  [l.name, l.hq, l.contact.name, l.contact.email, l.registration, l.industry ?? "", LEAD_KIND_LABEL[l.kind], l.source, l.equipment.join(" "), ...l.lanes.map((x) => `${x.origin} ${x.destination}`)]
    .join(" ")
    .toLowerCase()

export function KindBadge({ kind, industry }: { kind: LeadKind; industry?: string }) {
  const cls =
    kind === "Shipper" ? "border-asphalt bg-asphalt text-white" : kind === "Forwarder" ? "border-chart-4/50 bg-chart-4/10 text-chart-4" : "border-border bg-muted text-foreground"
  return (
    <span className="inline-flex max-w-full flex-col leading-tight">
      <span className={cn("inline-flex h-5 w-fit items-center rounded-[3px] border px-1.5 text-[0.68rem] font-semibold whitespace-nowrap", cls)}>{LEAD_KIND_LABEL[kind]}</span>
      {industry ? <span className="mt-0.5 truncate text-[0.68rem] text-muted-foreground">{industry}</span> : null}
    </span>
  )
}
