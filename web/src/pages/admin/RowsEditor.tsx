import { Braces, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Dialog, FamilyDot, Select, Textarea } from "@/components/ui";
import { type Meta } from "@/lib/api";

export type Row = Record<string, unknown>;

/** Table editor for row configurations; the common fields are columns, everything else is in the JSON view. */
export function RowsEditor({ rows, onChange, meta, withActivation }: { rows: Row[]; onChange: (r: Row[]) => void;
  meta: Meta; withActivation?: boolean }) {
  const [json, setJson] = useState<number | null>(null);
  const cfg = (r: Row): Row => (withActivation ? (r.config as Row) : r);
  const put = (i: number, patch: Row, act?: Row | null) => onChange(rows.map((r, j) => {
    if (j !== i) return r;
    if (!withActivation) return { ...r, ...patch };
    return { ...r, config: { ...(r.config as Row), ...patch }, ...(act !== undefined ? { activation: act } : {}) };
  }));
  const blank = (method = "rsi", direction = 1): Row => {
    const c = { method, direction, rung: "A1", max_positions: 10 };
    return withActivation ? { config: c, activation: null } : c;
  };
  const addSet = (key: string) => {
    const m = meta.catalogues[key]?.methods ?? [];
    onChange([...rows, ...m.flatMap((x) => [blank(x, 1), blank(x, -1)])]);
  };
  const fam = Object.fromEntries(meta.methods.map((m) => [m.method, m.family]));
  return (
    <div className="grid gap-3">
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-line bg-sunken text-left text-xs text-muted [&>th]:px-2 [&>th]:py-2 [&>th]:font-medium">
            <th>Method</th><th>Side</th><th>Rung</th><th>Positions</th><th>Sizing</th><th>Universe</th>
            {withActivation && <th>Edge on/off</th>}<th />
          </tr></thead>
          <tbody className="[&>tr]:border-b [&>tr]:border-line/60 [&_td]:px-2 [&_td]:py-1.5">
            {rows.map((r, i) => {
              const c = cfg(r);
              return (
                <tr key={i}>
                  <td><div className="flex items-center gap-2"><FamilyDot family={fam[String(c.method)] ?? "ENS"} />
                    <Select className="h-8 min-w-36" value={String(c.method)} onChange={(e) => put(i, { method: e.target.value })}>
                      {meta.families.map((f) => (
                        <optgroup key={f} label={f}>{meta.methods.filter((m) => m.family === f).map((m) =>
                          <option key={m.method} value={m.method}>{m.method}</option>)}</optgroup>))}
                    </Select></div></td>
                  <td><Select className="h-8 w-20" value={String(c.direction ?? 1)} onChange={(e) => put(i, { direction: Number(e.target.value) })}>
                    <option value="1">Buy</option><option value="-1">Sell</option></Select></td>
                  <td><Select className="h-8 w-20" value={String(c.rung ?? "A0")} onChange={(e) => put(i, { rung: e.target.value })}>
                    {meta.rungs.map((g) => <option key={g}>{g}</option>)}</Select></td>
                  <td><input type="number" min={0} className="h-8 w-20 rounded-md border border-line bg-surface px-2" value={Number(c.max_positions ?? 0)}
                    onChange={(e) => put(i, { max_positions: Number(e.target.value) })} /></td>
                  <td><Select className="h-8 w-24" value={String(c.sizing ?? "fixed")} onChange={(e) => put(i, { sizing: e.target.value })}>
                    <option value="fixed">Fixed</option><option value="vol">Vol target</option></Select></td>
                  <td><Select className="h-8 w-32" value={String(c.universe_mode ?? "membership")} onChange={(e) => put(i, { universe_mode: e.target.value })}>
                    <option value="membership">Index membership</option><option value="top_liquidity">Top liquidity</option></Select></td>
                  {withActivation && (
                    <td><Select className="h-8 w-32" value={String((r.activation as Row | null)?.mode ?? "")}
                      onChange={(e) => put(i, {}, e.target.value ? { ...(meta.activation_defaults as Row), mode: e.target.value } : null)}>
                      <option value="">Always on</option>
                      {meta.activation_modes.filter((m) => m !== "always").map((m) => <option key={m} value={m}>{m}</option>)}
                    </Select></td>)}
                  <td className="whitespace-nowrap text-right">
                    <Button size="sm" variant="ghost" onClick={() => setJson(i)} aria-label="Edit all fields"><Braces className="size-3.5" /></Button>
                    <Button size="sm" variant="ghost" onClick={() => onChange(rows.filter((_, j) => j !== i))} aria-label="Remove row"><Trash2 className="size-3.5" /></Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <p className="px-3 py-6 text-center text-sm text-muted">No rows yet.</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => onChange([...rows, blank()])}><Plus className="size-3.5" />Add row</Button>
        {!withActivation && <>
          <Button size="sm" onClick={() => addSet("equity")}>Add the 18 equity rows</Button>
          <Button size="sm" onClick={() => addSet("diverse")}>Add the diverse-family rows</Button>
        </>}
      </div>
      <JsonRow open={json !== null} row={json !== null ? rows[json] : null} onClose={() => setJson(null)}
        onSave={(v) => { if (json !== null) onChange(rows.map((r, j) => (j === json ? v : r))); setJson(null); }} />
    </div>
  );
}

function JsonRow({ open, row, onClose, onSave }: { open: boolean; row: Row | null; onClose: () => void; onSave: (r: Row) => void }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  return (
    <Dialog open={open} onOpenChange={(o) => { if (o) { setText(JSON.stringify(row, null, 2)); setErr(""); } else onClose(); }} title="All fields of this row">
      <OnOpen run={() => { setText(JSON.stringify(row, null, 2)); setErr(""); }} dep={row} />
      <p className="mb-3 text-sm text-muted">Any LadderConfig field (filters, structural, thresholds, exits, risk and universe settings). The server validates it on save.</p>
      <Textarea className="h-80 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} />
      {err && <p className="mt-2 text-sm text-neg">{err}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={() => { try { onSave(JSON.parse(text)); } catch (e) { setErr(`Not valid JSON: ${(e as Error).message}`); } }}>Apply</Button>
      </div>
    </Dialog>
  );
}

function OnOpen({ run, dep }: { run: () => void; dep: unknown }) {
  useEffect(run, [dep]);
  return null;
}
