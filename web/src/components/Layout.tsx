import { Activity, BookOpen, Boxes, Briefcase, Cable, Coins, FlaskConical, Gauge, Layers, Map, Menu, Moon,
  PlayCircle, Radio, Settings, ShieldHalf, Sun } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useApi } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const NAV: { group: string; items: { to: string; label: string; icon: ReactNode }[] }[] = [
  { group: "Workspace", items: [
    { to: "/", label: "Overview", icon: <Gauge /> },
    { to: "/runs", label: "Research runs", icon: <FlaskConical /> },
    { to: "/registry", label: "Trial registry", icon: <BookOpen /> },
    { to: "/live", label: "Paper & live", icon: <Radio /> },
    { to: "/jobs", label: "Jobs", icon: <PlayCircle /> },
  ] },
  { group: "Admin", items: [
    { to: "/admin/platform", label: "Platform", icon: <Settings /> },
    { to: "/admin/catalogues", label: "Catalogues", icon: <Layers /> },
    { to: "/admin/policies", label: "Policies", icon: <Briefcase /> },
    { to: "/admin/risk_budgets", label: "Risk budgets", icon: <ShieldHalf /> },
    { to: "/admin/cost_profiles", label: "Cost profiles", icon: <Coins /> },
    { to: "/admin/symbol_maps", label: "Symbol maps", icon: <Map /> },
    { to: "/admin/brokers", label: "Broker accounts", icon: <Cable /> },
    { to: "/admin/job_presets", label: "Job presets", icon: <Boxes /> },
  ] },
];

export function Layout() {
  const [open, setOpen] = useState(false);
  const { theme, toggle } = useTheme();
  const health = useApi<{ ok: boolean }>("/health", { refetchInterval: 15000 });
  return (
    <div className="flex h-full">
      <aside className={cn("fixed inset-y-0 left-0 z-30 w-60 shrink-0 border-r border-line bg-surface transition-transform md:static md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
          <svg viewBox="0 0 24 24" className="size-6 text-accent" aria-hidden="true">
            <path d="M3 17l5-6 4 4 7-9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M3 21h18" stroke="currentColor" strokeWidth="1.5" opacity=".4" />
          </svg>
          <div className="leading-tight">
            <div className="font-cond text-[15px] font-semibold">Strategy Factory</div>
            <div className="text-[11px] text-muted">Walk-forward research platform</div>
          </div>
        </div>
        <nav className="overflow-y-auto p-3" onClick={() => setOpen(false)}>
          {NAV.map((g) => (
            <div key={g.group} className="mb-5">
              <div className="mb-1.5 px-2 text-xs text-muted">{g.group}</div>
              {g.items.map((it) => (
                <NavLink key={it.to} to={it.to} end={it.to === "/"}
                  className={({ isActive }) => cn("flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm [&_svg]:size-4",
                    isActive ? "bg-accent-soft font-medium text-accent" : "text-ink/85 hover:bg-sunken")}>
                  {it.icon}{it.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      {open && <div className="fixed inset-0 z-20 bg-black/30 md:hidden" onClick={() => setOpen(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/80 px-4 backdrop-blur">
          <button className="md:hidden" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu className="size-5" /></button>
          <div className="flex-1" />
          <span className="inline-flex items-center gap-1.5 text-xs text-muted" title="Backend API">
            <Activity className={cn("size-3.5", health.data?.ok ? "text-pos" : "text-neg")} />
            {health.data?.ok ? "API connected" : health.isLoading ? "Connecting…" : "API unreachable"}
          </span>
          <button onClick={toggle} className="rounded-md p-2 hover:bg-sunken" aria-label="Toggle light and dark mode">
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] p-4 md:p-6"><Outlet /></div>
        </main>
      </div>
    </div>
  );
}

export function PageHead({ title, sub, action }: { title: string; sub?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-cond text-2xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
      </div>
      {action}
    </div>
  );
}
