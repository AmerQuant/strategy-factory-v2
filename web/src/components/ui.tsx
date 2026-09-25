/* Small component kit in the shadcn/ui style: Tailwind classes over Radix primitives, no runtime theme. */
import * as DialogP from "@radix-ui/react-dialog";
import * as SwitchP from "@radix-ui/react-switch";
import * as TabsP from "@radix-ui/react-tabs";
import { X } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn, FAMILIES } from "@/lib/utils";

type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
const BTN: Record<BtnVariant, string> = {
  primary: "bg-accent text-white hover:brightness-110 dark:text-[#06202b]",
  secondary: "bg-surface border border-line hover:bg-sunken",
  ghost: "hover:bg-sunken",
  danger: "bg-neg text-white hover:brightness-110",
};
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant; size?: "sm" | "md" }>(({ className, variant = "secondary", size = "md", ...p }, ref) => (
  <button ref={ref} className={cn("inline-flex items-center gap-1.5 rounded-md font-medium transition-colors",
    "disabled:opacity-50 disabled:pointer-events-none", size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
    BTN[variant], className)} {...p} />
));
Button.displayName = "Button";

const FIELD = "w-full rounded-md border border-line bg-surface px-3 text-sm placeholder:text-muted focus:border-accent";
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(FIELD, "h-9", className)} {...p} />
));
Input.displayName = "Input";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, ...p }, ref) => (
  <select ref={ref} className={cn(FIELD, "h-9 pr-2", className)} {...p} />
));
Select.displayName = "Select";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => <textarea ref={ref} className={cn(FIELD, "py-2 font-[inherit]", className)} {...p} />,
);
Textarea.displayName = "Textarea";

/** A labelled form control. `group` renders a div (for composite editors with their own buttons): a <label>
 * would forward every click inside it to its first control. */
export function Field({ label, hint, children, className, group }: { label: string; hint?: string; children: ReactNode;
  className?: string; group?: boolean }) {
  const Tag = group ? "div" : "label";
  return (
    <Tag className={cn("grid content-start gap-1.5 text-sm", className)} {...(group ? { role: "group", "aria-label": label } : {})}>
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </Tag>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="inline-flex items-center gap-2.5 text-sm">
      <SwitchP.Root checked={checked} onCheckedChange={onChange} aria-label={label}
        className="h-5 w-9 rounded-full bg-line data-[state=checked]:bg-accent transition-colors">
        <SwitchP.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
      </SwitchP.Root>
      {label}
    </label>
  );
}

/** A section of a page: a titled surface. Headings stay plain text; hierarchy comes from size and weight. */
export function Panel({ title, action, children, className, flush }: { title?: ReactNode; action?: ReactNode;
  children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={cn("rounded-lg border border-line bg-surface", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

export function FamilyDot({ family }: { family: string }) {
  const f = FAMILIES[family] ?? FAMILIES.ENS;
  return <span className="inline-block size-2.5 shrink-0 rounded-full" style={{ background: f.color }} title={f.label} />;
}

export function Tag({ tone = "neutral", children }: { tone?: "neutral" | "pos" | "neg" | "warn" | "accent";
  children: ReactNode }) {
  const t = {
    neutral: "bg-sunken text-muted", pos: "bg-pos/12 text-pos", neg: "bg-neg/12 text-neg",
    warn: "bg-warn/15 text-warn", accent: "bg-accent-soft text-accent",
  }[tone];
  return <span className={cn("inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium", t)}>{children}</span>;
}

export function Signed({ value, children }: { value: number | null | undefined; children: ReactNode }) {
  const v = Number(value);
  return <span className={cn(v > 0 ? "text-pos" : v < 0 ? "text-neg" : "")}>{children}</span>;
}

export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            {head.map((h, i) => <th key={i} className="whitespace-nowrap px-3 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-line/60 [&>tr:last-child]:border-0 [&_td]:px-3 [&_td]:py-2">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="grid place-items-center gap-2 px-6 py-14 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { value: string; label: string; content: ReactNode }[];
  value?: string; onChange?: (v: string) => void }) {
  return (
    <TabsP.Root value={value} defaultValue={tabs[0]?.value} onValueChange={onChange}>
      <TabsP.List className="mb-4 flex gap-1 border-b border-line">
        {tabs.map((t) => (
          <TabsP.Trigger key={t.value} value={t.value} className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted data-[state=active]:border-accent data-[state=active]:text-ink">
            {t.label}
          </TabsP.Trigger>
        ))}
      </TabsP.List>
      {tabs.map((t) => <TabsP.Content key={t.value} value={t.value}>{t.content}</TabsP.Content>)}
    </TabsP.Root>
  );
}

export function Dialog({ open, onOpenChange, title, children, wide }: { open: boolean; onOpenChange: (v: boolean) => void;
  title: string; children: ReactNode; wide?: boolean }) {
  return (
    <DialogP.Root open={open} onOpenChange={onOpenChange}>
      <DialogP.Portal>
        <DialogP.Overlay className="fixed inset-0 z-40 bg-[#0e1a2b]/50 backdrop-blur-[2px]" />
        <DialogP.Content className={cn("fixed left-1/2 top-[6vh] z-50 max-h-[88vh] w-[calc(100vw-2rem)] -translate-x-1/2 overflow-y-auto rounded-lg border border-line bg-surface shadow-2xl",
          wide ? "max-w-5xl" : "max-w-xl")}>
          <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-surface px-5 py-3">
            <DialogP.Title className="font-semibold">{title}</DialogP.Title>
            <DialogP.Close asChild><Button variant="ghost" size="sm" aria-label="Close"><X className="size-4" /></Button></DialogP.Close>
          </div>
          <DialogP.Description className="sr-only">{title}</DialogP.Description>
          <div className="p-5">{children}</div>
        </DialogP.Content>
      </DialogP.Portal>
    </DialogP.Root>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className="rounded-md bg-neg/10 px-3 py-2 text-sm text-neg">{error instanceof Error ? error.message : String(error)}</p>;
}
