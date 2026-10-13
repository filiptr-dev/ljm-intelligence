"use client"

import { useFormStatus } from "react-dom"
import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/** Submit button for the /emails Ask form; shows pending while the client-side navigation runs. */
export function AskButton() {
  const { pending } = useFormStatus()
  return (
    <button type="submit" disabled={pending} className={cn(buttonVariants(), "font-semibold")}>
      {pending ? "Asking…" : "Ask"}
    </button>
  )
}
