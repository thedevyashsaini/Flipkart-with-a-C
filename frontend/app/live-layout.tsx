"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

function Tab({ href, label }: { href: string; label: string }) {
  const pathname = usePathname()
  const active = pathname === href
  return (
    <Link
      href={href}
      className={`border-l border-border/70 px-3 py-2 text-center text-xs font-medium tracking-[0.08em] transition-colors last:border-r md:text-sm ${
        active
          ? "bg-foreground/10 text-foreground"
          : "bg-transparent text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
      }`}
    >
      {label}
    </Link>
  )
}

export function LiveTopNav() {
  return (
    <div className="border-b border-border/80 bg-card/95">
      <div className="flex min-h-10 items-center justify-between">
        <div className="px-3 text-xs font-medium tracking-[0.08em] text-muted-foreground md:text-sm">DEPLOYED SUPPLY CHAIN</div>
        <div className="flex h-full">
          <Tab href="/supply-admin" label="SUPPLY ADMIN" />
          <Tab href="/node-admin" label="NODE ADMIN" />
          <Tab href="/" label="STUDY ZONE" />
        </div>
      </div>
    </div>
  )
}
