"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import {
  Building2,
  Calculator,
  ChartColumnBig,
  ChevronDown,
  Container,
  Gauge,
  KanbanSquare,
  Mail,
  MapPinned,
  Package,
  PhoneCall,
  Radar,
  Route as RouteIcon,
  Send,
  Settings as SettingsIcon,
  Shield,
  Truck,
  UsersRound,
  Wallet,
  Waypoints,
} from "lucide-react"
import { HazardStripe, Wordmark } from "@/components/brand/marks"
import { Tire } from "@/components/brand/tire"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { CLIENT } from "@/lib/data/types"

// Primary = what the operator opens every day. Everything else collapses
// into "More tools". /campaigns and /outreach were removed as standalone
// pages 2026-10-13 — the backend outreach engine (app/outreach/*) stays;
// the next task turns the Freight Managers CTA into auto-reach.
const PRIMARY = [
  { href: "/loads", label: "Loads", icon: Package },
  { href: "/contacts/freight-managers", label: "Freight Managers", icon: UsersRound },
]
const MORE = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/intelligence", label: "Broker Insights", icon: ChartColumnBig },
  { href: "/intelligence/lanes", label: "Lanes history", icon: Waypoints },
  { href: "/brokers", label: "Brokers", icon: Building2 },
  { href: "/emails", label: "Email Analysis", icon: Mail },
  { href: "/messages", label: "Emails", icon: Send },
  { href: "/leads", label: "Lead Finder", icon: Radar },
  { href: "/fleet", label: "Fleet", icon: Truck },
  { href: "/capacity", label: "Capacity Posts", icon: Container },
  { href: "/call-list", label: "Call List", icon: PhoneCall },
  { href: "/shippers", label: "Shipper Finder", icon: MapPinned },
  { href: "/rates", label: "Lane Rate Calculator", icon: Calculator },
  { href: "/vetting", label: "Broker Check", icon: Shield },
  { href: "/backhaul", label: "Backhaul Finder", icon: RouteIcon },
  { href: "/profit", label: "Load Profit Calculator", icon: Wallet },
  { href: "/pipeline", label: "Follow-ups", icon: KanbanSquare },
]
const CONFIG = [{ href: "/settings", label: "Settings", icon: SettingsIcon }]
const MORE_STORAGE_KEY = "sidebar:more-open"

// Mail pages share a `?mailbox=` scope; preserve it across sidebar jumps
// (AC5 of the inbox-separation plan).
const MAIL_HREFS = new Set<string>(["/emails", "/messages"])

export function AppSidebar() {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const mailbox = searchParams.get("mailbox") || ""
  const mailQuery = mailbox ? `?mailbox=${encodeURIComponent(mailbox)}` : ""

  // `localStorage` can only be touched after mount; start closed to avoid
  // SSR hydration mismatch, then rehydrate from the saved pref.
  const [moreOpen, setMoreOpen] = useState<boolean>(false)
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(MORE_STORAGE_KEY)
      if (saved === "1") setMoreOpen(true)
      else if (saved === "0") setMoreOpen(false)
      else {
        // Default-open when the current path is one of the collapsed items
        // so the active state isn't invisible on first load.
        const hit = MORE.some((i) =>
          i.href === "/" ? pathname === "/" : pathname.startsWith(i.href),
        )
        if (hit) setMoreOpen(true)
      }
    } catch {
      /* non-browser / disabled storage — fall back to closed */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const toggleMore = () => {
    setMoreOpen((prev) => {
      const next = !prev
      try {
        window.localStorage.setItem(MORE_STORAGE_KEY, next ? "1" : "0")
      } catch {
        /* ignore */
      }
      return next
    })
  }

  const ALL_HREFS = [...PRIMARY, ...MORE, ...CONFIG].map((i) => i.href)
  const active = (href: string) =>
    href === "/"
      ? pathname === "/"
      : pathname.startsWith(href) &&
        !ALL_HREFS.some((h) => h.length > href.length && h.startsWith(href) && pathname.startsWith(h))

  const item = (i: { href: string; label: string; icon: React.ComponentType<{ className?: string }> }) => (
    <SidebarMenuItem key={i.href}>
      <SidebarMenuButton
        isActive={active(i.href)}
        tooltip={i.label}
        render={<Link href={MAIL_HREFS.has(i.href) ? `${i.href}${mailQuery}` : i.href} />}
        className="h-9 rounded-sm font-medium data-active:bg-sidebar-accent data-active:text-white data-active:shadow-[inset_3px_0_0_var(--safety)]"
      >
        <i.icon />
        <span>{i.label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )

  return (
    <Sidebar collapsible="icon" className="border-r-0">
      <HazardStripe id="hz-side" />
      <SidebarHeader className="px-3 pt-4 pb-3">
        <Link href="/" className="group-data-[collapsible=icon]:hidden">
          <Wordmark />
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="font-display tracking-[0.16em] text-[#8b9098]">Primary</SidebarGroupLabel>
          <SidebarMenu>{PRIMARY.map((i) => item(i))}</SidebarMenu>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                type="button"
                onClick={toggleMore}
                aria-expanded={moreOpen}
                aria-controls="sidebar-more-tools"
                tooltip="More tools"
                className="h-9 rounded-sm font-medium"
              >
                <ChevronDown
                  className={`transition-transform ${moreOpen ? "" : "-rotate-90"}`}
                />
                <span>More tools</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            {moreOpen ? (
              <div id="sidebar-more-tools">
                {MORE.map((i) => item(i))}
              </div>
            ) : null}
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel className="font-display tracking-[0.16em] text-[#8b9098]">Config</SidebarGroupLabel>
          <SidebarMenu>{CONFIG.map((i) => item(i))}</SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="p-3 group-data-[collapsible=icon]:hidden">
        <div className="rounded-sm border border-sidebar-border bg-[#1b1c20] p-3">
          <div className="flex items-center gap-2.5">
            <Tire className="size-8 shrink-0" />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-white">{CLIENT.company}</div>
              <div className="text-xs text-[#8b9098]">{CLIENT.fleet}</div>
            </div>
          </div>
          <div className="mt-3 flex items-center justify-between border-t border-sidebar-border pt-2 text-[0.7rem] text-[#8b9098]">
            <span>AI engine</span>
            <span className="font-mono text-[#d9d7d2]">Gemini</span>
          </div>
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
