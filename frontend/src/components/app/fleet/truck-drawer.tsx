"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ExternalLink, Pencil, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { EmptyChart } from "@/components/charts/primitives"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { deleteTruck, getTruckDetail, saveErrorMessage, type TruckDetail } from "@/lib/api/fleet"
import { TruckFormDialog } from "./truck-form-dialog"
import { TruckDetailView, UnitHeading } from "./truck-detail"

type Loaded = { id: number; detail: TruckDetail } | { id: number; error: string }

/** In-list quick peek. Deep links use /fleet/{id}; the header links there. */
export function TruckDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const router = useRouter()
  const [state, setState] = React.useState<Loaded | null>(null)
  const [editing, setEditing] = React.useState(false)
  const [confirming, setConfirming] = React.useState(false)
  const [removing, setRemoving] = React.useState(false)

  React.useEffect(() => {
    if (id == null) return
    const ctl = new AbortController()
    getTruckDetail(id, ctl.signal)
      .then((detail) => setState({ id, detail }))
      .catch((e) => { if (!ctl.signal.aborted) setState({ id, error: String(e?.message ?? e) }) })
    return () => ctl.abort()
  }, [id])

  // A result for a different unit means "still loading" the one that was just clicked.
  const cur = state && state.id === id ? state : null

  async function remove() {
    if (id == null) return
    setRemoving(true)
    try {
      await deleteTruck(id)
      toast.success("Unit removed from your fleet")
      setConfirming(false)
      onClose()
      router.refresh() // the list and the alerts strip are server-rendered
    } catch (e) {
      toast.error(saveErrorMessage(e))
    } finally {
      setRemoving(false)
    }
  }
  return (
    <Sheet open={id != null} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-[46rem]" data-testid="truck-drawer">
        <SheetHeader className="border-b border-border pr-12">
          <SheetTitle className="sr-only">Unit detail</SheetTitle>
          <SheetDescription className="sr-only">Inspections, defects, maintenance, documents and performance for this unit.</SheetDescription>
          {cur && "detail" in cur ? (
            <div className="space-y-2">
              <UnitHeading detail={cur.detail} />
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <Link href={`/fleet/${cur.id}`} className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
                  Open full page <ExternalLink className="size-3" aria-hidden />
                </Link>
                <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
                  <Pencil className="size-3.5" aria-hidden /> Edit
                </Button>
                <Button type="button" variant="outline" size="sm" className="text-destructive" onClick={() => setConfirming(true)}>
                  <Trash2 className="size-3.5" aria-hidden /> Remove
                </Button>
              </div>
            </div>
          ) : (
            <Skeleton className="h-8 w-56" />
          )}
        </SheetHeader>
        <div className="min-w-0 p-4">
          {cur && "detail" in cur ? (
            <TruckDetailView detail={cur.detail} />
          ) : cur ? (
            <EmptyChart title="Could not load this unit" hint={`The API did not answer (${cur.error}). Close the drawer and try again.`} />
          ) : (
            <div className="space-y-3" aria-hidden>
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-56 w-full" />
            </div>
          )}
        </div>
      </SheetContent>
      {cur && "detail" in cur ? (
        <>
          <TruckFormDialog
            open={editing}
            onOpenChange={setEditing}
            truck={cur.detail.truck}
            onSaved={(detail) => { setState({ id: cur.id, detail }); router.refresh() }}
          />
          <Dialog open={confirming} onOpenChange={setConfirming}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Remove {cur.detail.truck.unit_number}?</DialogTitle>
                <DialogDescription>
                  Its inspections, defects, maintenance and documents go with it. Past runs stay in your history, just no longer tied to this unit.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter className="flex-col-reverse gap-2 sm:flex-row">
                <Button type="button" variant="outline" onClick={() => setConfirming(false)} disabled={removing}>Keep it</Button>
                <Button type="button" variant="destructive" onClick={remove} disabled={removing}>{removing ? "Removing…" : "Remove unit"}</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      ) : null}
    </Sheet>
  )
}
