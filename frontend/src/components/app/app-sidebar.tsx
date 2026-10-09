"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Building2,
  Calculator,
  ChartColumnBig,
  Gauge,
  KanbanSquare,
  Mail,
  MapPinned,
  Megaphone,
  Package,
  PhoneCall,
  Radar,
  Route as RouteIcon,
  Send,
  Settings as SettingsIcon,
  Shield,
  SquarePen,
  Truck,
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
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { CLIENT } from "@/lib/data/types"
import { useEngine } from "./engine"

const ANALYSE = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/intelligence", label: "Broker Intelligence", icon: ChartColumnBig },
  { href: "/intelligence/lanes", label: "Lanes history", icon: Waypoints },
  { href: "/brokers", label: "Brokers", icon: Building2 },
  { href: "/emails", label: "Email Analysis", icon: Mail },
]
const GROW = [
  { href: "/leads", label: "Lead Finder", icon: Radar },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/outreach", label: "New campaign", icon: SquarePen },
  { href: "/messages", label: "Emails", icon: Send },
]
const TOOLS = [
  { href: "/loads", label: "Loads", icon: Package },
  { href: "/capacity", label: "Capacity Posts", icon: Truck },
  { href: "/call-list", label: "Call List", icon: PhoneCall },
  { href: "/shippers", label: "Shipper Finder", icon: MapPinned },
  { href: "/rates", label: "Lane Rate Calculator", icon: Calculator },
  { href: "/vetting", label: "Broker Check", icon: Shield },
  { href: "/backhaul", label: "Backhaul Finder", icon: RouteIcon },
  { href: "/profit", label: "Load Profit Calculator", icon: Wallet },
  { href: "/pipeline", label: "Follow-ups", icon: KanbanSquare },
]
const CONFIG = [{ href: "/settings", label: "Settings", icon: SettingsIcon }]

export function AppSidebar() {
  const pathname = usePathname()
  const { campaigns } = useEngine()
  // The longest matching href wins, so /intelligence/lanes doesn't also light up "Broker Intelligence".
  const ALL_HREFS = [...ANALYSE, ...GROW, ...TOOLS, ...CONFIG].map((i) => i.href)
  const active = (href: string) =>
    href === "/"
      ? pathname === "/"
      : pathname.startsWith(href) &&
        !ALL_HREFS.some((h) => h.length > href.length && h.startsWith(href) && pathname.startsWith(h))
  const sending = campaigns.filter((c) => !c.single).reduce((s, c) => s + c.recipients.filter((r) => r.status === "queued").length, 0)
  const newReplies = campaigns.filter((c) => c.single && c.recipients[0]?.reply).length
  // Lead Finder badge used to show `+liveLeads.length` (fake per-session count
  // from the client-side crawler simulation). Dropped per plan 2026-10-06; the
  // real "new leads today" number lives in the top-bar `found` counter.

  const item = (i: { href: string; label: string; icon: React.ComponentType<{ className?: string }> }, badge?: React.ReactNode) => (
    <SidebarMenuItem key={i.href}>
      <SidebarMenuButton
        isActive={active(i.href)}
        tooltip={i.label}
        render={<Link href={i.href} />}
        className="h-9 rounded-sm font-medium data-active:bg-sidebar-accent data-active:text-white data-active:shadow-[inset_3px_0_0_var(--safety)]"
      >
        <i.icon />
        <span>{i.label}</span>
      </SidebarMenuButton>
      {badge}
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
          <SidebarGroupLabel className="font-display tracking-[0.16em] text-[#8b9098]">Analyse</SidebarGroupLabel>
          <SidebarMenu>{ANALYSE.map((i) => item(i))}</SidebarMenu>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel className="font-display tracking-[0.16em] text-[#8b9098]">Grow</SidebarGroupLabel>
          <SidebarMenu>
            {item(GROW[0])}
            {item(GROW[1], sending ? <SidebarMenuBadge className="bg-sidebar-accent text-white">{sending}</SidebarMenuBadge> : null)}
            {item(GROW[2])}
            {item(GROW[3], newReplies ? <SidebarMenuBadge className="bg-good text-white">{newReplies}</SidebarMenuBadge> : null)}
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel className="font-display tracking-[0.16em] text-[#8b9098]">Tools</SidebarGroupLabel>
          <SidebarMenu>{TOOLS.map((i) => item(i))}</SidebarMenu>
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
