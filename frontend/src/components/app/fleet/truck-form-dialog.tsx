"use client"

import * as React from "react"
import { toast } from "sonner"
import { OPERATING_STATES_FALLBACK } from "@/components/charts/us-map-base"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { createTruck, saveErrorMessage, updateTruck, type TruckDetail } from "@/lib/api/fleet"
import { EQUIPMENT_LABEL, EQUIPMENT_ORDER, STATUS_LABEL, STATUS_ORDER } from "./format"

type TruckOut = TruckDetail["truck"]
const NONE = "none"
const KIND_LABEL: Record<string, string> = { truck: "Truck", trailer: "Trailer" }

type Form = {
  unit_number: string; kind: string; status: string; equipment: string; make: string; model: string; year: string
  vin: string; plate: string; odometer_miles: string; home_base_city: string; home_base_state: string; assigned_driver_name: string
}

const toForm = (t: TruckOut | null): Form => ({
  unit_number: t?.unit_number ?? "", kind: t?.kind ?? "truck", status: t?.status ?? "available", equipment: t?.equipment ?? NONE,
  make: t?.make ?? "", model: t?.model ?? "", year: t?.year?.toString() ?? "", vin: t?.vin ?? "", plate: t?.plate ?? "",
  odometer_miles: t?.odometer_miles?.toString() ?? "", home_base_city: t?.home_base_city ?? "",
  home_base_state: t?.home_base_state ?? NONE, assigned_driver_name: t?.driver_name ?? "",
})

const num = (v: string) => (v.trim() === "" ? null : Number(v))
const text = (v: string) => v.trim() || null

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <Label className="mb-1.5 text-[0.8rem] text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

function Pick({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <Select value={value} onValueChange={(v) => v && onChange(v)}>
      <SelectTrigger className="w-full"><SelectValue>{options.find(([v]) => v === value)?.[1]}</SelectValue></SelectTrigger>
      <SelectContent>{options.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
    </Select>
  )
}

/** Add a unit (`truck` null) or edit one. The API validates; its message is shown as-is. */
export function TruckFormDialog({ open, onOpenChange, truck, onSaved }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  truck: TruckOut | null
  onSaved: (detail: TruckDetail) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        {/* The popup unmounts when closed, so the form state starts fresh from `truck` on every open. */}
        <TruckForm truck={truck} onClose={() => onOpenChange(false)} onSaved={onSaved} />
      </DialogContent>
    </Dialog>
  )
}

function TruckForm({ truck, onClose, onSaved }: { truck: TruckOut | null; onClose: () => void; onSaved: (detail: TruckDetail) => void }) {
  const [f, setF] = React.useState<Form>(() => toForm(truck))
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const set = <K extends keyof Form>(k: K) => (v: string) => setF((s) => ({ ...s, [k]: v }))
  const input = (k: keyof Form, props: React.ComponentProps<typeof Input> = {}) => (
    <Input value={f[k]} onChange={(e) => set(k)(e.target.value)} {...props} />
  )

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body = {
      unit_number: f.unit_number.trim(), kind: f.kind as "truck" | "trailer",
      status: f.status as "available" | "on_load" | "in_shop" | "out_of_service",
      equipment: f.equipment === NONE ? null : (f.equipment as "van" | "reefer" | "flatbed" | "stepdeck"),
      make: text(f.make), model: text(f.model), year: num(f.year), vin: text(f.vin), plate: text(f.plate),
      odometer_miles: num(f.odometer_miles), home_base_city: text(f.home_base_city),
      home_base_state: f.home_base_state === NONE ? null : f.home_base_state, assigned_driver_name: text(f.assigned_driver_name),
    }
    try {
      const detail = truck ? await updateTruck(truck.id, body) : await createTruck(body)
      toast.success(truck ? `${detail.truck.unit_number} saved` : `${detail.truck.unit_number} added to your fleet`)
      onSaved(detail)
      onClose()
    } catch (err) {
      setError(saveErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle className="font-display text-xl">{truck ? `Edit ${truck.unit_number}` : "Add a truck"}</DialogTitle>
            <DialogDescription>Only the unit number is required. The rest can be filled in later.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Unit number *">{input("unit_number", { required: true, maxLength: 32, autoFocus: true })}</Field>
            <Field label="Type"><Pick value={f.kind} onChange={set("kind")} options={Object.entries(KIND_LABEL)} /></Field>
            <Field label="Status"><Pick value={f.status} onChange={set("status")} options={STATUS_ORDER.map((s) => [s, STATUS_LABEL[s]])} /></Field>
            <Field label="Make">{input("make", { maxLength: 64 })}</Field>
            <Field label="Model">{input("model", { maxLength: 64 })}</Field>
            <Field label="Year">{input("year", { type: "number", min: 1980, max: 2100 })}</Field>
            <Field label="Equipment">
              <Pick value={f.equipment} onChange={set("equipment")} options={[[NONE, "Not set"], ...EQUIPMENT_ORDER.map((e): [string, string] => [e, EQUIPMENT_LABEL[e]])]} />
            </Field>
            <Field label="VIN">{input("vin", { maxLength: 32 })}</Field>
            <Field label="Plate">{input("plate", { maxLength: 24 })}</Field>
            <Field label="Odometer (miles)">{input("odometer_miles", { type: "number", min: 0 })}</Field>
            <Field label="Home base city">{input("home_base_city", { maxLength: 128 })}</Field>
            <Field label="Home base state">
              <Pick value={f.home_base_state} onChange={set("home_base_state")} options={[[NONE, "Not set"], ...OPERATING_STATES_FALLBACK.map((s): [string, string] => [s, s])]} />
            </Field>
            <Field label="Assigned driver" className="sm:col-span-3">{input("assigned_driver_name", { maxLength: 128 })}</Field>
          </div>
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
          <DialogFooter className="flex-col-reverse gap-2 sm:flex-row">
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || !f.unit_number.trim()}>{busy ? "Saving…" : truck ? "Save changes" : "Add truck"}</Button>
          </DialogFooter>
        </form>
    </>
  )
}
