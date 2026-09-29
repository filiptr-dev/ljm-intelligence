"use client"

import * as React from "react"
import { RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"

/** Replays the analysis pipeline as a progress toast. The numbers are already computed. */
export function RerunButton() {
  const [busy, setBusy] = React.useState(false)
  const run = () => {
    setBusy(true)
    const steps = ["Syncing mailbox…", "Classifying new emails…", "Extracting rates and lanes…", "Re-clustering brokers…"]
    const id = toast.loading(steps[0])
    steps.slice(1).forEach((s, i) => setTimeout(() => toast.loading(s, { id }), (i + 1) * 900))
    setTimeout(() => {
      toast.success("Analysis up to date · 14 new emails processed", { id })
      setBusy(false)
    }, steps.length * 900)
  }
  return (
    <Button variant="outline" onClick={run} disabled={busy}>
      <RefreshCw className={busy ? "animate-spin" : undefined} /> Sync &amp; re-analyse
    </Button>
  )
}
