import { cn } from "@/lib/utils"
import type { Region } from "@/lib/data/geo"

/**
 * LJM lockup body — the speed-lines + italic LJM + roofline/exhaust + truck-cab silhouette,
 * without the INTELLIGENCE wordmark. Everything renders in `currentColor` so the same
 * paths work as white-on-charcoal in the sidebar, brand-red on light, or inverted for print.
 *
 * Letters are hand-authored polygon paths (not a webfont) so the mark renders identically
 * on any host — even if Barlow Condensed hasn't loaded yet.
 */
function LjmLockupBody() {
  return (
    <>
      {/* Three tapered speed lines on the left */}
      <polygon points="0,110 250,130 0,150" />
      <polygon points="0,200 250,220 0,240" />
      <polygon points="0,290 250,310 0,330" />

      {/* Tapered roofline running above the wordmark into the exhaust stack */}
      <polygon points="380,22 1170,44 1170,58 380,56" />

      {/* Exhaust stack (pipe + cap) rising from the cab roof */}
      <rect x="1160" y="8" width="18" height="80" />
      <rect x="1152" y="0" width="34" height="14" />

      {/* Heavy italic LJM wordmark, skewed for right-leaning slant */}
      <g transform="translate(430 0) skewX(-14)">
        {/* L */}
        <path d="M 0 70 L 100 70 L 100 340 L 230 340 L 230 415 L 0 415 Z" />
        {/* J */}
        <path d="M 270 70 L 435 70 L 435 335 C 435 385, 400 415, 345 415 C 285 415, 250 380, 250 335 L 320 335 C 320 365, 335 385, 355 385 C 375 385, 375 370, 375 335 L 375 135 L 270 135 Z" />
        {/* M */}
        <path d="M 475 70 L 565 70 L 625 230 L 685 70 L 775 70 L 775 415 L 705 415 L 705 220 L 660 345 L 590 345 L 545 220 L 545 415 L 475 415 Z" />
      </g>

      {/* Truck-cab silhouette: back wall, roof with aero fairing dip, steep windshield wedge, long hood, bumper */}
      <path d="M 1170 58 L 1170 110 L 1195 110 L 1205 86 L 1245 86 L 1258 100 L 1268 96 L 1282 110 L 1400 300 L 1400 415 L 1305 415 L 1305 335 L 1285 335 L 1285 155 L 1200 155 L 1200 415 L 1170 415 Z" />
    </>
  )
}

/**
 * Full LJM Intelligence lockup — used in the sidebar header, marketing, PDF headers.
 * Monochrome, `currentColor` on every fill; no gradients, no drop-shadows.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 1400 640" className={cn("h-9 w-auto", className)} fill="currentColor" role="img" aria-label="LJM Intelligence">
      <LjmLockupBody />
      <text
        x="700"
        y="590"
        textAnchor="middle"
        fontFamily="var(--font-heading), 'Barlow Condensed', 'Arial Narrow', Impact, sans-serif"
        fontWeight="700"
        fontSize="185"
        letterSpacing="6"
        style={{ fontStretch: "condensed" }}
      >
        INTELLIGENCE
      </text>
    </svg>
  )
}

/**
 * Compact mark — just italic LJM, for the collapsed sidebar, chips, favicon parity.
 */
export function LogoMarkCompact({ className }: { className?: string }) {
  return (
    <svg viewBox="290 60 800 370" className={cn("h-8 w-auto", className)} fill="currentColor" role="img" aria-label="LJM">
      <g transform="translate(430 0) skewX(-14)">
        <path d="M 0 70 L 100 70 L 100 340 L 230 340 L 230 415 L 0 415 Z" />
        <path d="M 270 70 L 435 70 L 435 335 C 435 385, 400 415, 345 415 C 285 415, 250 380, 250 335 L 320 335 C 320 365, 335 385, 355 385 C 375 385, 375 370, 375 335 L 375 135 L 270 135 Z" />
        <path d="M 475 70 L 565 70 L 625 230 L 685 70 L 775 70 L 775 415 L 705 415 L 705 220 L 660 345 L 590 345 L 545 220 L 545 415 L 475 415 Z" />
      </g>
    </svg>
  )
}

/**
 * The header lockup used across the app: the full mark + a subtitle underneath.
 * `dark` controls contrast against dark vs light chrome.
 *
 * Why one component: the sidebar, topbar and marketing pages all read from here — one
 * knob to twist when the brand shifts again.
 */
export function Wordmark({ className, dark = true }: { className?: string; dark?: boolean }) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <LogoMark className={cn("h-12 w-auto self-start", dark ? "text-white" : "text-foreground")} />
      <div className={cn("text-[0.6rem] font-semibold tracking-[0.22em] uppercase", dark ? "text-[#8b9098]" : "text-muted-foreground")}>
        Broker intelligence · Eastern US
      </div>
    </div>
  )
}

/**
 * Red/charcoal hazard band — a utility pattern (not a brand mark). Now uses LJM brand red.
 * Kept as a role: activity / motion indicator.
 */
export function HazardStripe({ className, id = "hz" }: { className?: string; id?: string }) {
  return (
    <svg className={cn("block h-1.5 w-full", className)} preserveAspectRatio="none" aria-hidden>
      <defs>
        <pattern id={id} width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="8" height="16" fill="#2B2B2B" />
          <rect x="8" width="8" height="16" fill="#BC2444" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  )
}

/** Asphalt strip with a dashed centre line, now in LJM red; animates while "driving". */
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
      <circle cx="60" cy="60" r="2.2" fill="var(--safety)" />
    </svg>
  )
}
