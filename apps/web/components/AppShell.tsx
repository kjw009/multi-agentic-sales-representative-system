"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { MessageSquare, Inbox, Settings, LogOut, Menu, X, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface AppShellProps {
  children: React.ReactNode;
  /** Shown in the right-hand panel (e.g. pricing sidebar on /chat). */
  panel?: React.ReactNode;
  ebayConnected?: boolean;
  onConnectEbay?: () => void;
  connectingEbay?: boolean;
  inboxCount?: number;
}

const NAV_ITEMS = [
  { href: "/chat",     label: "Chat",     Icon: MessageSquare },
  { href: "/inbox",    label: "Inbox",    Icon: Inbox },
  { href: "/settings", label: "Settings", Icon: Settings },
];

export function AppShell({
  children,
  panel,
  ebayConnected,
  onConnectEbay,
  connectingEbay,
  inboxCount,
}: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);

  function logout() {
    localStorage.clear();
    router.push("/login");
  }

  const sidebar = (
    <div className="flex flex-col h-full p-4 gap-3">
      {/* Brand */}
      <div className="flex items-center justify-between">
        <p className="font-semibold text-sm">SalesRep</p>
        {/* Close button — mobile only */}
        <button
          className="lg:hidden text-muted-foreground hover:text-foreground"
          onClick={() => setMobileOpen(false)}
        >
          <X size={18} />
        </button>
      </div>

      {/* Nav */}
      <nav className="space-y-0.5 mt-4">
        {NAV_ITEMS.map(({ href, label, Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              onClick={() => setMobileOpen(false)}
              className={cn(
                "flex items-center gap-2.5 w-full text-sm rounded-lg px-3 py-2 transition-colors",
                active
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              )}
            >
              <Icon size={15} className="shrink-0" />
              <span>{label}</span>
              {label === "Inbox" && inboxCount && inboxCount > 0 ? (
                <span className="ml-auto inline-flex items-center justify-center bg-primary text-primary-foreground text-xs font-bold px-1.5 py-0.5 rounded-full min-w-[20px]">
                  {inboxCount}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>

      <div className="flex-1" />

      {/* eBay status */}
      {onConnectEbay !== undefined && (
        ebayConnected ? (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
            <CheckCircle2 size={14} className="shrink-0" />
            <span>eBay Connected</span>
          </div>
        ) : (
          <Button
            size="sm"
            onClick={onConnectEbay}
            disabled={connectingEbay}
            className="w-full"
          >
            {connectingEbay ? "Redirecting…" : "Connect eBay"}
          </Button>
        )
      )}

      {/* Logout */}
      <button
        onClick={logout}
        className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground px-1 transition-colors"
      >
        <LogOut size={14} />
        <span>Log out</span>
      </button>
    </div>
  );

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/40 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar — desktop: always visible, mobile: slide-in drawer */}
      <aside
        className={cn(
          "w-56 bg-card border-r border-border shrink-0 z-30",
          "fixed inset-y-0 left-0 lg:static transition-transform duration-200",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        )}
      >
        {sidebar}
      </aside>

      {/* Main area */}
      <div className="flex flex-1 min-w-0 overflow-hidden">
        {/* Mobile hamburger */}
        <button
          className="absolute top-3 left-3 z-10 lg:hidden text-muted-foreground hover:text-foreground p-1"
          onClick={() => setMobileOpen(true)}
        >
          <Menu size={20} />
        </button>

        {/* Content */}
        <main className="flex-1 min-w-0 overflow-y-auto">
          {children}
        </main>

        {/* Optional right panel */}
        {panel && (
          <aside className="w-80 shrink-0 bg-background border-l border-border p-5 overflow-y-auto hidden xl:block">
            {panel}
          </aside>
        )}
      </div>
    </div>
  );
}
