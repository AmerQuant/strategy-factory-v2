import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));

export function num(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  if (!Number.isFinite(n)) return "–";
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(v: unknown, digits = 1): string {
  if (v === null || v === undefined) return "–";
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "–";
}

export function money(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "–";
  const s = Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  return `${n < 0 ? "−" : ""}$${s}`;
}

export function when(iso?: string | null): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export const FAMILIES: Record<string, { label: string; color: string }> = {
  MR: { label: "Mean reversion", color: "var(--color-fam-mr)" },
  TF: { label: "Trend following", color: "var(--color-fam-tf)" },
  VOL: { label: "Volatility", color: "var(--color-fam-vol)" },
  XS: { label: "Cross-sectional", color: "var(--color-fam-xs)" },
  CAL: { label: "Calendar", color: "var(--color-fam-cal)" },
  EV: { label: "Events", color: "var(--color-fam-ev)" },
  ENS: { label: "Ensemble", color: "var(--color-fam-ens)" },
};

export function familyOf(rowId: string): string {
  const f = rowId.split("-")[0];
  return f in FAMILIES ? f : "ENS";
}

/** Resolve a CSS variable to a concrete colour (charts need real values). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}
