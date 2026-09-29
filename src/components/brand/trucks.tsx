import { cn } from "@/lib/utils"
import { TireGlyph } from "./tire"

const INK = "#16171a"
const CHROME = "#c9ccd1"
const GLASS = "#2b2d31"

/** American long-nose conventional tractor + 53' dry van. */
export function USTruck({ className, cab = "#f5b800", spinning = false }: { className?: string; cab?: string; spinning?: boolean }) {
  return (
    <svg viewBox="0 0 330 112" className={cn("w-full", className)} aria-hidden>
      {/* trailer */}
      <rect x="4" y="8" width="200" height="70" rx="2" fill="#f4f2ee" stroke={INK} strokeWidth="2.5" />
      {[32, 60, 88, 116, 144, 172].map((x) => (
        <line key={x} x1={x} y1="10" x2={x} y2="76" stroke="#d8d4cb" strokeWidth="1.5" />
      ))}
      <rect x="4" y="78" width="200" height="6" fill={INK} />
      <rect x="150" y="84" width="4" height="16" fill={INK} />
      <rect x="144" y="98" width="16" height="3" fill={INK} />
      {/* exhaust stack + air intake */}
      <rect x="238" y="0" width="5" height="46" rx="2" fill={CHROME} stroke={INK} strokeWidth="1.5" />
      {/* sleeper */}
      <path d="M208 24 h34 v56 h-34 z" fill={cab} stroke={INK} strokeWidth="2.5" strokeLinejoin="round" />
      <rect x="214" y="32" width="10" height="16" rx="1" fill={GLASS} />
      {/* cab */}
      <path d="M242 30 h20 l10 20 v30 h-30 z" fill={cab} stroke={INK} strokeWidth="2.5" strokeLinejoin="round" />
      <path d="M248 35 h12 l7 14 h-19 z" fill={GLASS} />
      {/* long hood + grille */}
      <path d="M272 50 h38 q8 0 8 8 v22 h-46 z" fill={cab} stroke={INK} strokeWidth="2.5" strokeLinejoin="round" />
      <rect x="312" y="54" width="7" height="24" rx="1" fill={CHROME} stroke={INK} strokeWidth="1.5" />
      {[58, 63, 68, 73].map((y) => (
        <line key={y} x1="313" y1={y} x2="318" y2={y} stroke={INK} strokeWidth="1" />
      ))}
      <rect x="286" y="46" width="10" height="4" rx="1" fill={CHROME} stroke={INK} strokeWidth="1" />
      <rect x="306" y="80" width="18" height="6" rx="2" fill={CHROME} stroke={INK} strokeWidth="1.5" />
      {/* fuel tank */}
      <rect x="244" y="74" width="28" height="11" rx="5" fill={CHROME} stroke={INK} strokeWidth="1.5" />
      <rect x="204" y="80" width="112" height="5" fill={INK} />
      {/* wheels */}
      <TireGlyph cx={34} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={64} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={218} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={248} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={298} cy={96} r={14} spinning={spinning} />
    </svg>
  )
}

/** European cab-over tractor + curtain-side (tautliner) trailer, tri-axle. */
export function EUTruck({ className, cab = "#2f63a8", spinning = false }: { className?: string; cab?: string; spinning?: boolean }) {
  return (
    <svg viewBox="0 0 330 112" className={cn("w-full", className)} aria-hidden>
      {/* curtain trailer */}
      <rect x="4" y="6" width="226" height="72" rx="2" fill="#f4f2ee" stroke={INK} strokeWidth="2.5" />
      {Array.from({ length: 13 }, (_, i) => 20 + i * 16).map((x) => (
        <line key={x} x1={x} y1="8" x2={x} y2="76" stroke="#cfcac0" strokeWidth="2" />
      ))}
      <rect x="4" y="6" width="226" height="7" fill={INK} />
      <rect x="4" y="78" width="226" height="6" fill={INK} />
      <rect x="170" y="84" width="4" height="16" fill={INK} />
      {/* cab-over */}
      <path d="M240 10 h66 q10 0 10 10 v58 h-76 z" fill={cab} stroke={INK} strokeWidth="2.5" strokeLinejoin="round" />
      <path d="M284 18 h20 q6 0 6 6 v22 h-26 z" fill={GLASS} />
      <rect x="248" y="18" width="30" height="22" rx="2" fill="none" stroke={INK} strokeWidth="1.5" />
      <rect x="316" y="30" width="5" height="18" rx="1" fill={INK} />
      <rect x="300" y="58" width="16" height="12" rx="1" fill={CHROME} stroke={INK} strokeWidth="1.5" />
      <rect x="236" y="78" width="88" height="7" rx="1" fill={INK} />
      <rect x="258" y="60" width="26" height="12" rx="3" fill={CHROME} stroke={INK} strokeWidth="1.5" />
      {/* wheels: tri-axle trailer, 4x2 tractor */}
      <TireGlyph cx={40} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={70} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={100} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={252} cy={96} r={14} spinning={spinning} />
      <TireGlyph cx={302} cy={96} r={14} spinning={spinning} />
    </svg>
  )
}
