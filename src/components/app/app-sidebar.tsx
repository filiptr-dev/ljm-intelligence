"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Building2, ChartColumnBig, Gauge, Mail, Megaphone, Radar, Send, SquarePen, Truck } from "lucide-react"
import { HazardStripe, Wordmark } from "@/components/brand/marks"
import { Tire } from "@/components/brand/tire"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader,
  SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem,
} from "@/components/ui/sidebar"
import { CLIENT } from "@/lib/data/types"
import { useEngine } from "./engine"

const ANALYSE = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/intelligence", label: "Broker Intelligence", icon: ChartColumnBig },
  { href: "/brokers", label: "Brokers", icon: Building2 },
  { href: "/emails", label: "Email Analysis", icon: Mail },
]
const GROW = [
  { href: "/leads", label: "Lead Finder", icon: Radar },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/outreach", label: "New campaign", icon: SquarePen },
  { href: "/messages", label: "Emails", icon: Send },
  { href: "/capacity", label: "Capacity Posts", icon: Truck },
]

export function AppSidebar() {
  const pathname = usePathname()
  const { liveLeads, campaigns } = useEngine()
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href))
  const sending = campaigns.filter((c) => !c.single).reduce((s, c) => s + c.recipients.filter((r) => r.status === "queued").length, 0)
  const newReplies = campaigns.filter((c) => c.single && c.recipients[0]?.reply).length

  const item = (i: (typeof ANALYSE)[number], badge?: React.ReactNode) => (
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
            {item(GROW[0], liveLeads.length ? <SidebarMenuBadge className="bg-safety text-asphalt">+{liveLeads.length}</SidebarMenuBadge> : null)}
            {item(GROW[1], sending ? <SidebarMenuBadge className="bg-sidebar-accent text-white">{sending}</SidebarMenuBadge> : null)}
            {item(GROW[2])}
            {item(GROW[3], newReplies ? <SidebarMenuBadge className="bg-good text-white">{newReplies}</SidebarMenuBadge> : null)}
            {item(GROW[4])}
          </SidebarMenu>
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
