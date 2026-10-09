"use client"

import * as React from "react"
import Link from "next/link"
import { ExternalLink } from "lucide-react"
import { EmptyChart } from "@/components/charts/primitives"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { getTruckDetail, type TruckDetail } from "@/lib/api/fleet"
import { TruckDetailView, UnitHeading } from "./truck-detail"

type Loaded = { id: number; detail: TruckDetail } | { id: number; error: string }

/** In-list quick peek. Deep links use /fleet/{id}; the header links there. */
export function TruckDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const [state, setState] = React.useState<Loaded | null>(null)

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
  return (
    <Sheet open={id != null} onOpenChange={(o) => { if (!o) onClose() }}>
      <SheetContent className="w-full gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-[46rem]" data-testid="truck-drawer">
        <SheetHeader className="border-b border-border pr-12">
          <SheetTitle className="sr-only">Unit detail</SheetTitle>
          <SheetDescription className="sr-only">Inspections, defects, maintenance, documents and performance for this unit.</SheetDescription>
          {cur && "detail" in cur ? (
            <div className="space-y-2">
              <UnitHeading detail={cur.detail} />
              <Link href={`/fleet/${cur.id}`} className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
                Open full page <ExternalLink className="size-3" aria-hidden />
              </Link>
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
    </Sheet>
  )
}
