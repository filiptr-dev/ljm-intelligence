"use client"

import { cn } from "@/lib/utils"

/** Small segmented control (single choice). */
export function Segmented<T extends string>({
  value, onChange, options, className,
}: { value: T; onChange: (v: T) => void; options: { value: T; label: React.ReactNode }[]; className?: string }) {
  return (
    <div role="radiogroup" className={cn("inline-flex rounded-sm border border-input bg-background p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-7 rounded-[3px] px-3 text-sm font-medium whitespace-nowrap transition-colors",
            value === o.value ? "bg-asphalt text-white" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
