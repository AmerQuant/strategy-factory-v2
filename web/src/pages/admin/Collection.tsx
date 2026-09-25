import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { PageHead } from "@/components/Layout";
import { Button, Dialog, Empty, ErrorNote, Field, Input, Panel, Select, Switch, Table, Tag, Textarea } from "@/components/ui";
import { type Meta, useApi, useSave } from "@/lib/api";
import { type Row, RowsEditor } from "./RowsEditor";

type Doc = Record<string, unknown> & { id: string; name: string };
type FieldSpec = { key: string; label: string;
  type: "text" | "number" | "optnumber" | "bool" | "select" | "textarea" | "strlist" | "numlist";
  options?: [string, string][]; hint?: string };

const KIND_FLAGS: Record<string, string[]> = {
  run_real: ["store", "out", "registry", "timeframe", "resample", "clock-shift", "symbols", "top-n", "membership", "dividends",
    "costs", "methods", "diverse", "rung", "max-positions", "max-new", "no-ensembles", "no-robustness", "open-holdout",
    "analyze-accepted", "max-row-weight", "max-family-weight", "target-vol", "workers", "asset-class", "symbol-top-n", "sessions"],
  run_daily: ["state", "init", "policy", "first-dp", "dp-months", "is-years", "store", "membership", "dividends", "costs", "broker",
    "phase", "day", "report-dir", "mt5-login", "mt5-server", "symbol-map", "dry-run", "asset-class"],
  run_intraday: ["state", "init", "policy", "first-dp", "dp-months", "is-years", "store", "timeframe", "resample", "clock-shift",
    "membership", "dividends", "costs", "broker", "until", "skip-last", "report-dir", "mt5-login", "mt5-server", "symbol-map", "dry-run",
    "asset-class"],
  check_survivorship: ["store", "timeframe", "membership", "out"],
  convert_costs: ["v1-costs", "universe", "store", "prices", "notional", "out", "map-out"],
  bench_speed: ["symbols", "store", "workers", "rows"],
  export_mt5_specs: ["symbol-map", "symbols", "out", "timeframe", "bars", "mt5-login", "mt5-server", "mt5-path"],
  convert_mt5_costs: ["specs", "manual", "spread-stat", "out", "map-out"],
  refresh_v1_data: ["v1-repo", "steps", "runner", "data-root", "timeout"],
};

interface Spec { title: string; single: string; sub: string; fields: FieldSpec[]; columns: [string, (d: Doc) => ReactNode][];
  blank: () => Doc }

const SPECS: Record<string, Spec> = {
  catalogues: {
    title: "Catalogues", single: "catalogue", sub: "Pre-registered row sets. Adding a row after seeing results is a new trial, so version them.",
    fields: [{ key: "version", label: "Version", type: "text", hint: "Bump when rows change" }],
    columns: [["Version", (d) => String(d.version)], ["Rows", (d) => (d.rows as unknown[]).length]],
    blank: () => ({ id: "", name: "", description: "", version: "v1", rows: [] }),
  },
  policies: {
    title: "Policies", single: "policy", sub: "Rows to trade, optionally with an accepted edge on/off mechanism. Freeze a policy before paper trading.",
    fields: [{ key: "source_run", label: "Source run", type: "text" }, { key: "frozen", label: "Frozen", type: "bool" }],
    columns: [["Entries", (d) => (d.entries as unknown[]).length], ["Source run", (d) => String(d.source_run || "–")],
      ["State", (d) => (d.frozen ? <Tag tone="accent">frozen</Tag> : <Tag>draft</Tag>)]],
    blank: () => ({ id: "", name: "", description: "", entries: [], source_run: "", frozen: false }),
  },
  risk_budgets: {
    title: "Risk budgets", single: "risk budget", sub: "Caps for the combined policy: per row, per edge family and a portfolio volatility target.",
    fields: [{ key: "max_weight", label: "Max weight per row", type: "optnumber", hint: "0 to 1, empty for none" },
      { key: "family_cap", label: "Max weight per family", type: "optnumber", hint: "0 to 1, empty for none" },
      { key: "target_vol_ann", label: "Target volatility per year", type: "optnumber", hint: "For example 0.10" },
      { key: "lev_max", label: "Maximum leverage", type: "number" }],
    columns: [["Row cap", (d) => String(d.max_weight ?? "–")], ["Family cap", (d) => String(d.family_cap ?? "–")],
      ["Vol target", (d) => String(d.target_vol_ann ?? "–")]],
    blank: () => ({ id: "", name: "", description: "", max_weight: 0.25, family_cap: 0.5, target_vol_ann: null, lev_max: 2 }),
  },
  cost_profiles: {
    title: "Cost profiles", single: "cost profile", sub: "Default costs in basis points per side and swap in % per year. Per-symbol files come from the Moneta converter.",
    fields: [{ key: "spread_bps", label: "Spread (bps, full)", type: "number" }, { key: "commission_bps", label: "Commission (bps per side)", type: "number" },
      { key: "slippage_bps", label: "Slippage (bps per side)", type: "number" }, { key: "swap_long_pct", label: "Swap long (% per year)", type: "number" },
      { key: "swap_short_pct", label: "Swap short (% per year)", type: "number" }, { key: "overrides_csv", label: "Per-symbol CSV", type: "text" }],
    columns: [["Spread", (d) => `${d.spread_bps} bps`], ["Swap long", (d) => `${d.swap_long_pct}%`], ["Overrides", (d) => String(d.overrides_csv || "–")]],
    blank: () => ({ id: "", name: "", description: "", spread_bps: 2, commission_bps: 0, slippage_bps: 1, swap_long_pct: -6.88,
      swap_short_pct: -3.5, overrides_csv: "" }),
  },
  symbol_maps: {
    title: "Symbol maps", single: "symbol map", sub: "Research symbol to broker symbol, one pair per line (AAPL=AAPL.US).",
    fields: [], columns: [["Symbols", (d) => Object.keys(d.mapping as object).length]],
    blank: () => ({ id: "", name: "", description: "", mapping: {} }),
  },
  brokers: {
    title: "Broker accounts", single: "broker account", sub: "Where orders go. The password is read from SF_MT5_PASSWORD at run time, never stored.",
    fields: [{ key: "kind", label: "Type", type: "select", options: [["sim", "Simulated (paper)"], ["mt5", "MetaTrader 5"]] },
      { key: "login", label: "MT5 login", type: "optnumber" }, { key: "server", label: "MT5 server", type: "text" },
      { key: "symbol_map", label: "Symbol map id", type: "text" }, { key: "dry_run", label: "Dry run (build orders, do not send)", type: "bool" }],
    columns: [["Type", (d) => String(d.kind)], ["Server", (d) => String(d.server || "–")],
      ["Mode", (d) => (d.dry_run ? <Tag tone="warn">dry run</Tag> : <Tag tone="neg">live orders</Tag>)]],
    blank: () => ({ id: "", name: "", description: "", kind: "sim", login: null, server: "", symbol_map: "", dry_run: true }),
  },
  schedules: {
    title: "Schedules", single: "schedule", sub: "Chains of job presets on New York market time, run by the scheduler service (python -m sfactory.scheduler).",
    fields: [
      { key: "steps", label: "Job presets, in order", type: "strlist", hint: "Preset ids separated by commas; the chain stops at the first failure" },
      { key: "enabled", label: "Enabled", type: "bool" },
      { key: "kind", label: "Trigger", type: "select", options: [["daily", "Daily at a time"], ["bars", "After every bar"]] },
      { key: "tz", label: "Time zone", type: "text", hint: "Market time; daylight saving is followed" },
      { key: "time", label: "Time (daily)", type: "text", hint: "HH:MM, e.g. 16:30 after the close" },
      { key: "weekdays", label: "Weekdays", type: "numlist", hint: "0 = Monday ... 6 = Sunday" },
      { key: "every_minutes", label: "Bar length in minutes (bars)", type: "number" },
      { key: "delay_minutes", label: "Delay after the bar close (bars)", type: "number", hint: "Time for the data refresh" },
      { key: "session_start", label: "Session start (bars)", type: "text", hint: "HH:MM, start of the first bar" },
      { key: "session_end", label: "Session end (bars)", type: "text", hint: "HH:MM, close of the last bar" },
      { key: "misfire", label: "If a run was missed", type: "select", options: [["skip", "Record it as missed (live)"], ["run_once", "Run it once late (paper)"]] },
      { key: "grace_minutes", label: "Grace period in minutes", type: "number", hint: "Later than this counts as missed" },
    ],
    columns: [["Trigger", (d) => d.kind === "daily" ? `daily ${d.time}` : `every ${d.every_minutes} min + ${d.delay_minutes}`],
      ["Steps", (d) => (d.steps as string[]).join(" → ")],
      ["State", (d) => (d.enabled ? <Tag tone="pos">enabled</Tag> : <Tag>paused</Tag>)]],
    blank: () => ({ id: "", name: "", description: "", enabled: true, steps: [], kind: "daily", time: "16:30", tz: "America/New_York",
      weekdays: [0, 1, 2, 3, 4], every_minutes: 60, session_start: "09:30", session_end: "16:00", delay_minutes: 5,
      misfire: "skip", grace_minutes: 10 }),
  },
  job_presets: {
    title: "Job presets", single: "job preset", sub: "Saved command lines for the platform scripts. Start them from Jobs.",
    fields: [{ key: "kind", label: "Script", type: "select", options: [["run_real", "Research run (run_real)"],
      ["run_daily", "Daily paper / live job (run_daily)"], ["run_intraday", "Intraday paper / live job (run_intraday)"],
      ["check_survivorship", "Survivorship check"], ["convert_costs", "Moneta cost converter"], ["bench_speed", "Speed benchmark"],
      ["export_mt5_specs", "MT5 symbol specification export"], ["convert_mt5_costs", "MT5 cost converter (FX / index / metals)"],
      ["refresh_v1_data", "v1 data refresh (sfac)"]] }],
    columns: [["Script", (d) => String(d.kind)], ["Arguments", (d) => Object.keys(d.args as object).length]],
    blank: () => ({ id: "", name: "", description: "", kind: "run_real", args: {} }),
  },
};

export function Collection() {
  const { collection = "" } = useParams();
  const spec = SPECS[collection];
  const docs = useApi<Doc[]>(spec ? `/config/${collection}` : null);
  const meta = useApi<Meta>("/meta");
  const [edit, setEdit] = useState<{ doc: Doc; isNew: boolean } | null>(null);
  const del = useSave<void>([`/config/${collection}`]);
  if (!spec) return <Empty title="Unknown section" />;
  return (
    <>
      <PageHead title={spec.title} sub={spec.sub} action={
        <Button variant="primary" onClick={() => setEdit({ doc: spec.blank(), isNew: true })}><Plus className="size-4" />New</Button>} />
      <Panel flush>
        {docs.data?.length ? (
          <Table head={["Name", "Id", ...spec.columns.map((c) => c[0]), ""]}>
            {docs.data.map((d) => (
              <tr key={d.id}>
                <td><button className="font-medium text-accent" onClick={() => setEdit({ doc: d, isNew: false })}>{d.name}</button>
                  {d.description ? <div className="text-xs text-muted">{String(d.description)}</div> : null}</td>
                <td className="text-muted">{d.id}</td>
                {spec.columns.map(([k, f]) => <td key={k}>{f(d)}</td>)}
                <td className="whitespace-nowrap text-right">
                  <Button size="sm" variant="ghost" onClick={() => setEdit({ doc: d, isNew: false })} aria-label="Edit"><Pencil className="size-3.5" /></Button>
                  <Button size="sm" variant="ghost" aria-label="Delete" onClick={() => {
                    if (confirm(`Delete ${d.name}?`)) del.mutate({ method: "del", path: `/config/${collection}/${d.id}` },
                      { onSuccess: () => toast.success(`Deleted ${d.name}`), onError: (e) => toast.error(e.message) });
                  }}><Trash2 className="size-3.5" /></Button>
                </td>
              </tr>
            ))}
          </Table>
        ) : <Empty title={`No ${spec.title.toLowerCase()} yet`}>Create the first one with New.</Empty>}
      </Panel>
      {edit && meta.data && <Editor collection={collection} spec={spec} meta={meta.data} init={edit.doc} isNew={edit.isNew} onClose={() => setEdit(null)} />}
    </>
  );
}

function Editor({ collection, spec, meta, init, isNew, onClose }: { collection: string; spec: Spec; meta: Meta; init: Doc;
  isNew: boolean; onClose: () => void }) {
  const [doc, setDoc] = useState<Doc>(structuredClone(init));
  const save = useSave<Doc>([`/config/${collection}`]);
  const set = (k: string, v: unknown) => setDoc((d) => ({ ...d, [k]: v }));
  const clean = collection === "job_presets"
    ? { ...doc, args: Object.fromEntries(Object.entries(doc.args as object).filter(([k]) => k)) } : doc;
  const submit = () => save.mutate(
    { method: isNew ? "post" : "put", path: isNew ? `/config/${collection}` : `/config/${collection}/${doc.id}`, body: clean },
    { onSuccess: () => { toast.success(`Saved ${doc.name}`); onClose(); } });
  const wide = collection === "catalogues" || collection === "policies" || collection === "job_presets";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={isNew ? `New ${spec.single}` : doc.name} wide={wide}>
      <div className="grid gap-4">
        <div className="grid items-start gap-4 sm:grid-cols-2">
          <Field label="Name"><Input value={doc.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Id" hint="Lowercase letters, digits, - and _"><Input value={doc.id} disabled={!isNew} onChange={(e) => set("id", e.target.value)} /></Field>
        </div>
        <Field label="Description"><Input value={String(doc.description ?? "")} onChange={(e) => set("description", e.target.value)} /></Field>
        {spec.fields.length > 0 && <div className="grid items-start gap-4 sm:grid-cols-2">
          {spec.fields.map((f) => <FieldInput key={f.key} f={f} value={doc[f.key]} onChange={(v) => set(f.key, v)} />)}
        </div>}
        {collection === "catalogues" && <Field group label="Rows"><RowsEditor rows={doc.rows as Row[]} meta={meta} onChange={(r) => set("rows", r)} /></Field>}
        {collection === "policies" && <Field group label="Entries"><RowsEditor rows={doc.entries as Row[]} meta={meta} withActivation onChange={(r) => set("entries", r)} /></Field>}
        {collection === "symbol_maps" && <MappingInput value={doc.mapping as Record<string, string>} onChange={(m) => set("mapping", m)} />}
        {collection === "job_presets" && <ArgsInput kind={String(doc.kind)} value={doc.args as Record<string, unknown>} onChange={(a) => set("args", a)} />}
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={save.isPending || !doc.id || !doc.name} onClick={submit}>{isNew ? "Create" : "Save changes"}</Button>
        </div>
      </div>
    </Dialog>
  );
}

function FieldInput({ f, value, onChange }: { f: FieldSpec; value: unknown; onChange: (v: unknown) => void }) {
  if (f.type === "bool") return <div className="flex items-end pb-2"><Switch checked={Boolean(value)} onChange={onChange} label={f.label} /></div>;
  if (f.type === "select") return (
    <Field label={f.label} hint={f.hint}><Select value={String(value)} onChange={(e) => onChange(e.target.value)}>
      {f.options?.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>);
  if (f.type === "strlist" || f.type === "numlist") return (
    <ListInput f={f} value={(value as unknown[]) ?? []} onChange={onChange} />);
  if (f.type === "textarea") return <Field label={f.label} hint={f.hint}><Textarea value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} /></Field>;
  const numeric = f.type === "number" || f.type === "optnumber";
  return (
    <Field label={f.label} hint={f.hint}>
      <Input type={numeric ? "number" : "text"} step="any" value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(numeric ? (e.target.value === "" ? (f.type === "optnumber" ? null : 0) : Number(e.target.value)) : e.target.value)} />
    </Field>
  );
}

function ListInput({ f, value, onChange }: { f: FieldSpec; value: unknown[]; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(value.join(", "));
  const parse = (t: string) => {
    const parts = t.split(",").map((x) => x.trim()).filter(Boolean);
    return f.type === "numlist" ? parts.map(Number).filter((x) => Number.isFinite(x)) : parts;
  };
  return (
    <Field label={f.label} hint={f.hint}>
      <Input value={text} onChange={(e) => { setText(e.target.value); onChange(parse(e.target.value)); }} />
    </Field>
  );
}

function MappingInput({ value, onChange }: { value: Record<string, string>; onChange: (m: Record<string, string>) => void }) {
  const [text, setText] = useState(Object.entries(value).map(([a, b]) => `${a}=${b}`).join("\n"));
  const parse = (t: string) => Object.fromEntries(t.split("\n").map((l) => l.trim()).filter((l) => l.includes("="))
    .map((l) => l.split("=").map((x) => x.trim()) as [string, string]));
  return (
    <Field label="Mapping" hint={`${Object.keys(parse(text)).length} symbols. One RESEARCH=BROKER pair per line; the Moneta converter writes these too.`}>
      <Textarea className="h-64 font-mono text-xs" value={text} onChange={(e) => { setText(e.target.value); onChange(parse(e.target.value)); }} />
    </Field>
  );
}

function ArgsInput({ kind, value, onChange }: { kind: string; value: Record<string, unknown>; onChange: (a: Record<string, unknown>) => void }) {
  const entries = Object.entries(value);
  const flags = KIND_FLAGS[kind] ?? [];
  const put = (list: [string, unknown][]) => onChange(Object.fromEntries(list.filter(([k]) => k)));
  return (
    <Field group label="Arguments" hint="Flags without values (such as diverse or open-holdout) are switched on with the checkbox.">
      <div className="grid gap-2">
        <datalist id={`flags-${kind}`}>{flags.map((f) => <option key={f} value={f} />)}</datalist>
        {entries.map(([k, v], i) => (
          <div key={i} className="flex items-center gap-2">
            <Input list={`flags-${kind}`} className="w-56" value={k} placeholder="flag"
              onChange={(e) => put(entries.map((x, j) => (j === i ? [e.target.value, x[1]] : x)))} />
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={v === true} onChange={(e) => put(entries.map((x, j) => (j === i ? [x[0], e.target.checked ? true : ""] : x)))} />on
            </label>
            <Input value={v === true ? "" : String(v ?? "")} disabled={v === true} placeholder="value"
              onChange={(e) => put(entries.map((x, j) => (j === i ? [x[0], e.target.value] : x)))} />
            <Button size="sm" variant="ghost" aria-label="Remove argument" onClick={() => put(entries.filter((_, j) => j !== i))}><Trash2 className="size-3.5" /></Button>
          </div>
        ))}
        <div><Button size="sm" onClick={() => onChange({ ...value, "": "" })}><Plus className="size-3.5" />Add argument</Button></div>
      </div>
    </Field>
  );
}
