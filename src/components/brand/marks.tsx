import { cn } from "@/lib/utils"
import type { Region } from "@/lib/data/geo"

/** Badge emblem: road-sign octagon with a radar sweep over a highway. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={cn("size-9", className)} aria-hidden>
      <path d="M15 2 h18 l13 13 v18 l-13 13 h-18 l-13 -13 v-18 z" fill="#f5b800" stroke="#16171a" strokeWidth="2" />
      <path d="M16.5 6 h15 l10.5 10.5 v15 l-10.5 10.5 h-15 l-10.5 -10.5 v-15 z" fill="none" stroke="#16171a" strokeWidth="1.5" />
      <path d="M13 30 a11 11 0 0 1 22 0" fill="none" stroke="#16171a" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M18 30 a6 6 0 0 1 12 0" fill="none" stroke="#16171a" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M24 30 L32 19" stroke="#16171a" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="24" cy="30" r="2.6" fill="#16171a" />
      <path d="M10 35 h28" stroke="#16171a" strokeWidth="2.5" />
    </svg>
  )
}

export function Wordmark({ className, dark = true }: { className?: string; dark?: boolean }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <LogoMark />
      <div className="leading-none">
        <div className={cn("font-display text-[1.35rem] font-bold tracking-wide", dark ? "text-white" : "text-foreground")}>
          Freight<span className="text-safety">Radar</span>
        </div>
        <div className={cn("mt-0.5 text-[0.6rem] font-semibold tracking-[0.22em] uppercase", dark ? "text-[#8b9098]" : "text-muted-foreground")}>
          Broker intelligence
        </div>
      </div>
    </div>
  )
}

/** Yellow/black hazard band, as an SVG pattern (flat fills, no gradients). */
export function HazardStripe({ className, id = "hz" }: { className?: string; id?: string }) {
  return (
    <svg className={cn("block h-1.5 w-full", className)} preserveAspectRatio="none" aria-hidden>
      <defs>
        <pattern id={id} width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="8" height="16" fill="#16171a" />
          <rect x="8" width="8" height="16" fill="#f5b800" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  )
}

/** Asphalt strip with a dashed centre line; animates while "driving". */
export function Road({ className, moving = false }: { className?: string; moving?: boolean }) {
  return (
    <div className={cn("relative h-3 overflow-hidden bg-asphalt", className)} aria-hidden>
      <div className={cn("absolute inset-y-0 -right-12 left-0 flex items-center gap-6 pl-2", moving && "animate-road")}>
        {Array.from({ length: 80 }, (_, i) => (
          <span key={i} className="h-[3px] w-6 shrink-0 bg-safety" />
        ))}
      </div>
    </div>
  )
}

/** Registration shown as a licence plate: US MC-number plate or EU plate with the blue band. */
export function Plate({ region, value, country, className }: { region: Region; value: string; country: string; className?: string }) {
  if (region === "US") {
    const label = value.startsWith("DUNS") ? "D-U-N-S NUMBER" : value.startsWith("FF") ? "FREIGHT FORWARDER" : "MOTOR CARRIER"
    return (
      <span className={cn("inline-flex flex-col items-center rounded-[4px] border-2 border-asphalt bg-white px-1.5 pt-px pb-0.5 leading-none", className)}>
        <span className="text-[0.5rem] font-bold tracking-[0.18em] text-chart-1">{label}</span>
        <span className="font-mono text-[0.78rem] font-semibold text-asphalt">{value}</span>
      </span>
    )
  }
  return (
    <span className={cn("inline-flex items-stretch overflow-hidden rounded-[4px] border-2 border-asphalt bg-white leading-none", className)}>
      <span className="flex w-4 flex-col items-center justify-end bg-[#1f4aa8] pb-0.5 text-[0.5rem] font-bold text-white">{country}</span>
      <span className="px-1.5 py-1 font-mono text-[0.72rem] font-semibold text-asphalt">{value}</span>
    </span>
  )
}

/** Truck-dash gauge. Value 0–100; the arc fill carries severity, the track is recessive. */
export function Gauge({ value, className, label }: { value: number; className?: string; label?: string }) {
  const v = Math.max(0, Math.min(100, value))
  const angle = -180 + (v / 100) * 180
  const color = v >= 70 ? "var(--good)" : v >= 45 ? "var(--chart-2)" : "var(--bad)"
  const arc = (from: number, to: number) => {
    const p = (deg: number) => [60 + 46 * Math.cos((deg * Math.PI) / 180), 60 + 46 * Math.sin((deg * Math.PI) / 180)]
    const [x1, y1] = p(from)
    const [x2, y2] = p(to)
    return `M${x1} ${y1} A46 46 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`
  }
  return (
    <svg viewBox="0 0 120 70" className={cn("w-28", className)} role="img" aria-label={`${label ?? "Score"} ${Math.round(v)} of 100`}>
      <path d={arc(-180, 0)} fill="none" stroke="var(--grid)" strokeWidth="9" />
      {v > 0 ? <path d={arc(-180, angle)} fill="none" stroke={color} strokeWidth="9" /> : null}
      {Array.from({ length: 11 }, (_, i) => {
        const a = ((-180 + i * 18) * Math.PI) / 180
        const long = i % 5 === 0
        return (
          <line key={i} x1={60 + 36 * Math.cos(a)} y1={60 + 36 * Math.sin(a)} x2={60 + (long ? 29 : 32) * Math.cos(a)} y2={60 + (long ? 29 : 32) * Math.sin(a)} stroke="#16171a" strokeWidth={long ? 2 : 1.2} />
        )
      })}
      <g transform={`rotate(${angle} 60 60)`}>
        <path d="M60 57 L100 60 L60 63 z" fill="#16171a" />
      </g>
      <circle cx="60" cy="60" r="6" fill="#16171a" />
      <circle cx="60" cy="60" r="2.2" fill="#f5b800" />
    </svg>
  )
}
