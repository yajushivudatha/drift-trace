"use client";

import { Moon, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "New trace" },
  { href: "/library", label: "Library" },
  { href: "/review", label: "Review" },
  { href: "/costs", label: "Costs" },
];

export function ThemeToggle() {
  const [dark, setDark] = React.useState(false);
  React.useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);
  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("drifttrace-theme", next ? "dark" : "light");
    } catch {
      // The choice simply won't persist.
    }
  };
  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}>
      {dark ? <Sun /> : <Moon />}
    </Button>
  );
}

/** The mark is the product in miniature: a line that changes color as it goes. */
export function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
      <svg width="22" height="14" viewBox="0 0 22 14" aria-hidden>
        <path d="M1 7h6" stroke="var(--d0)" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M8 7c2 0 2-3 4-3" stroke="var(--d1)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
        <path d="M14 3.5c2 0 2 7 6 7" stroke="var(--d3)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
      </svg>
      DriftTrace
    </Link>
  );
}

export function TopBar() {
  const pathname = usePathname();
  return (
    <header className="no-print sticky top-0 z-30 flex h-12 items-center gap-4 border-b border-line bg-surface px-4">
      <Wordmark />
      <nav className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto" aria-label="Main">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={pathname === item.href ? "page" : undefined}
            className={cn(
              "whitespace-nowrap rounded-[5px] px-2.5 py-1 text-ink-2 hover:text-ink",
              pathname === item.href && "bg-sunken font-medium text-ink",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      <ThemeToggle />
    </header>
  );
}
