import { ExternalLink, FilePlus2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { axis, Chart } from "@/components/Chart";
import { PageHead } from "@/components/Layout";
import { Button, Dialog, Empty, FamilyDot, Panel, Signed, Table, Tabs, Tag } from "@/components/ui";
import { type Evidence, type EvidenceRow, useApi, useSave } from "@/lib/api";
import { familyOf, num, pct, when } from "@/lib/utils";

const ACCEPTED = (r: EvidenceRow) => r.path === "standalone" || r.path === "portfolio";

function PathTag({ path }: { path: string | null }) {
  if (!path) return <span className="text-muted">–</span>;
  if (path.includes("rejected")) return <Tag tone="neg">Rejected by robustness</Tag>;
  return <Tag tone="pos">{path === "standalone" ? "Standalone" : "Portfolio"}</Tag>;
}

export function RunDetail() {
  const { id = "" } = useParams();
  const ev = useApi<Evidence>(`/runs/${id}`);
  const [row, setRow] = useState<string | null>(null);
  const [onlyAccepted, setOnlyAccepted] = useState(false);
  const [sort, setSort] = useState<keyof EvidenceRow>("sharpe");
  const nav = useNavigate();
  const save = useSave<{ id: string }>(["/config/policies"]);

  const rows = useMemo(() => {
    const r = [...(ev.data?.rows ?? [])].filter((x) => !onlyAccepted || ACCEPTED(x));
    return r.sort((a, b) => Number(b[sort] ?? -1e9) - Number(a[sort] ?? -1e9));
  }, [ev.data, onlyAccepted, sort]);

  if (ev.isError) return <Empty title="Run not found">{ev.error.message}</Empty>;
  const d = ev.data;
  if (!d) return null;
  const meta = d.meta as Record<string, unknown>;

  const makePolicy = () => save.mutate({ method: "post", path: `/runs/${id}/policy`, body: {} }, {
    onSuccess: (p) => { toast.success(`Policy ${p.id} created`); nav("/admin/policies"); },
    onError: (e) => toast.error(e.message),
  });

  const sortHead = (k: keyof EvidenceRow, label: string) => (
    <button className={sort === k ? "text-ink" : ""} onClick={() => setSort(k)}>{label}</button>
  );

  return (
    <>
      <PageHead title={id} sub={<>Generated {when(d.generated_at)}, {String(meta.timeframe ?? "")} bars, catalogue {String(meta.catalog ?? "")}</>}
        action={<div className="flex gap-2">
          {d.has_report && <a href={`/api/runs/${id}/report`} target="_blank" rel="noreferrer"><Button><ExternalLink className="size-4" />Open report</Button></a>}
          <Button variant="primary" onClick={makePolicy} disabled={!d.holdout || save.isPending}
            title={d.holdout ? "Create a policy from the frozen holdout rows" : "Open the holdout first (run_real --open-holdout)"}>
            <FilePlus2 className="size-4" />Create policy
          </Button>
        </div>} />

      <Tabs tabs={[
        { value: "rows", label: "Rows", content: (
          <Panel flush title={`${rows.length} configurations`} action={
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={onlyAccepted} onChange={(e) => setOnlyAccepted(e.target.checked)} />Accepted only
            </label>}>
            <Table head={["Row", "Path", sortHead("sharpe", "Sharpe"), sortHead("dsr", "DSR"), sortHead("p", "p-value"), "BH",
              sortHead("max_dd", "Max drawdown"), sortHead("n", "Trades"), sortHead("expectancy", "Expectancy")]}>
              {rows.map((r) => (
                <tr key={r.row + r.rung} className="cursor-pointer hover:bg-sunken/60" onClick={() => setRow(r.row)}>
                  <td><span className="flex items-center gap-2 font-medium"><FamilyDot family={familyOf(r.row)} />{r.row}</span></td>
                  <td><PathTag path={r.path} /></td>
                  <td><Signed value={r.sharpe}>{num(r.sharpe)}</Signed></td>
                  <td>{num(r.dsr, 3)}</td>
                  <td>{num(r.p, 4)}</td>
                  <td>{r.bh ? <Tag tone="pos">pass</Tag> : <span className="text-muted">–</span>}</td>
                  <td>{pct(r.max_dd)}</td>
                  <td>{r.n}</td>
                  <td><Signed value={r.expectancy}>{num(r.expectancy, 1)}</Signed></td>
                </tr>
              ))}
            </Table>
          </Panel>) },
        { value: "charts", label: "Evidence", content: <EvidenceCharts d={d} /> },
        { value: "robust", label: "Robustness", content: <Robustness d={d} /> },
        { value: "analysis", label: "Edge & sizing", content: <Analysis d={d} /> },
        { value: "meta", label: "Run settings", content: (
          <Panel><pre className="overflow-x-auto text-xs leading-relaxed">{JSON.stringify({ meta, holdout: d.holdout,
            risk_budget: d.combined_dev.risk_budget }, null, 2)}</pre></Panel>) },
      ]} />

      <RowDialog runId={id} row={row} onClose={() => setRow(null)} />
    </>
  );
}

function EvidenceCharts({ d }: { d: Evidence }) {
  const rows = [...d.rows].sort((a, b) => b.sharpe - a.sharpe);
  const w = d.combined_dev.weights_per_fold ?? [];
  const names = [...new Set(w.flatMap((x) => Object.keys(x)))];
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Panel title="Out-of-sample Sharpe per configuration">
        <Chart height={Math.max(260, rows.length * 18)} option={(c) => ({
          grid: { left: 190, right: 24, top: 8, bottom: 28 }, tooltip: { trigger: "item" },
          xAxis: { type: "value", ...axis(c) },
          yAxis: { type: "category", inverse: true, data: rows.map((r) => r.row), ...axis(c, { splitLine: { show: false } }),
            axisLabel: { color: c.muted, fontSize: 11 } },
          series: [{ type: "bar", barMaxWidth: 12, data: rows.map((r) => ({ value: r.sharpe,
            itemStyle: { color: c.fam[familyOf(r.row)], opacity: ACCEPTED(r) ? 1 : 0.35 } })) }],
        })} />
        <p className="mt-2 text-xs text-muted">Solid bars passed the gates; faded bars did not. Colour is the edge family.</p>
      </Panel>
      <Panel title="Deflated Sharpe against raw Sharpe">
        <Chart height={320} option={(c) => ({
          tooltip: { trigger: "item", formatter: (p: { data: [number, number, string] }) => `${p.data[2]}<br/>Sharpe ${p.data[0].toFixed(2)}, DSR ${p.data[1].toFixed(3)}` },
          xAxis: { type: "value", name: "Sharpe", nameLocation: "middle", nameGap: 26, ...axis(c) },
          yAxis: { type: "value", name: "DSR", min: 0, max: 1, ...axis(c) },
          series: [{ type: "scatter", symbolSize: 10, data: d.rows.map((r) => ({ value: [r.sharpe, r.dsr ?? 0, r.row],
            itemStyle: { color: c.fam[familyOf(r.row)], opacity: ACCEPTED(r) ? 1 : 0.45 } })),
            markLine: { silent: true, symbol: "none", lineStyle: { color: c.muted, type: "dashed" },
              data: [{ yAxis: 0.95, label: { formatter: "DSR gate 0.95", color: c.muted, position: "insideEndTop" } }] } }],
        })} />
      </Panel>
      {names.length > 0 && (
        <Panel title="Combined policy weights per fold" className="lg:col-span-2">
          <Chart height={Math.max(200, names.length * 28 + 60)} option={(c) => ({
            grid: { left: 190, right: 60, top: 10, bottom: 40 }, tooltip: { trigger: "item" },
            xAxis: { type: "category", data: w.map((_, i) => `Fold ${i + 1}`), ...axis(c, { splitLine: { show: false } }) },
            yAxis: { type: "category", data: names, ...axis(c, { splitLine: { show: false } }) },
            visualMap: { min: 0, max: Math.max(...w.flatMap((x) => Object.values(x)), 0.01), orient: "vertical", right: 0, top: 10,
              calculable: false, itemHeight: 120, textStyle: { color: c.muted }, inRange: { color: [c.surface, c.accent] } },
            series: [{ type: "heatmap", data: w.flatMap((x, i) => names.map((n, j) => [i, j, x[n] ?? 0])),
              label: { show: true, color: c.ink, fontSize: 10, formatter: (p: { data: number[] }) => (p.data[2] ? p.data[2].toFixed(2) : "") } }],
          })} />
          {d.combined_dev.leverage_per_fold?.some((l) => l !== 1) && (
            <p className="mt-2 text-xs text-muted">Leverage from the risk budget: {d.combined_dev.leverage_per_fold.map((l) => num(l)).join(", ")}</p>
          )}
        </Panel>
      )}
      {d.family_ensembles && (
        <Panel title="Family ensembles against their best member" className="lg:col-span-2" flush>
          <Table head={["Family and side", "Ensemble Sharpe", "Best member", "Best member Sharpe", "Ensemble better"]}>
            {Object.entries(d.family_ensembles).map(([k, e]) => (
              <tr key={k}>
                <td><span className="flex items-center gap-2"><FamilyDot family={k.split("-")[0]} />{k}</span></td>
                <td>{num(e.sharpe)}</td><td>{e.best_member}</td><td>{num(e.best_member_sharpe)}</td>
                <td>{e.ensemble_better ? <Tag tone="pos">yes</Tag> : <Tag>no</Tag>}</td>
              </tr>
            ))}
          </Table>
        </Panel>
      )}
    </div>
  );
}

function Check({ ok }: { ok: boolean | undefined }) {
  return ok ? <Tag tone="pos">pass</Tag> : <Tag tone="neg">fail</Tag>;
}

function Robustness({ d }: { d: Evidence }) {
  const r = d.robustness_of_accepted_rows ?? {};
  if (!Object.keys(r).length) return <Panel><Empty title="No robustness results">Robustness runs only for accepted rows and needs dividends input (use --no-robustness to skip it).</Empty></Panel>;
  return (
    <Panel flush>
      <Table head={["Row", "Cost ×1.5", "MC drawdown p95", "Cost ×2", "1-bar delay keeps ≥50%", "Noise keeps ≥50%", "No catastrophic regime", "Delay keep"]}>
        {Object.entries(r).map(([row, x]) => (
          <tr key={row}>
            <td><span className="flex items-center gap-2 font-medium"><FamilyDot family={familyOf(row)} />{row}</span></td>
            <td><Check ok={x.mandatory["cost_x1.5_profitable"]} /></td>
            <td><Check ok={x.mandatory.mc_dd_p95_within_limit} /> <span className="text-xs text-muted">{pct(x.mc_dd_p95)}</span></td>
            <td><Check ok={x.warnings.cost_x2_profitable} /></td>
            <td><Check ok={x.warnings.delay_keeps_half} /></td>
            <td><Check ok={x.warnings.noise_keeps_half} /></td>
            <td><Check ok={x.warnings.no_catastrophic_regime} /></td>
            <td>{pct(x.delay_keep, 0)}</td>
          </tr>
        ))}
      </Table>
    </Panel>
  );
}

function Analysis({ d }: { d: Evidence }) {
  const s = d.row_analysis_summary;
  if (!s || !Object.keys(s).length) return <Panel><Empty title="No edge or sizing analysis">Start the run with --analyze-accepted to test edge on/off mechanisms and sizing for every accepted row.</Empty></Panel>;
  return (
    <Panel flush>
      <Table head={["Row", "State persistence", "Accepted edge mechanisms", "Accepted sizing variants"]}>
        {Object.entries(s).map(([row, a]) => (
          <tr key={row}>
            <td><span className="flex items-center gap-2 font-medium"><FamilyDot family={familyOf(row)} />{row}</span></td>
            <td>{a.persistent === "persistent" ? <Tag tone="pos">persistent</Tag> : <Tag>not persistent</Tag>}</td>
            <td>{a.edge_accepted.length ? a.edge_accepted.join(", ") : <span className="text-muted">none</span>}</td>
            <td>{a.sizing_accepted.length ? a.sizing_accepted.join(", ") : <span className="text-muted">none</span>}</td>
          </tr>
        ))}
      </Table>
      <p className="px-4 py-3 text-xs text-muted">Accepted mechanisms are candidates only; add them to a policy entry to trade them.</p>
    </Panel>
  );
}

function RowDialog({ runId, row, onClose }: { runId: string; row: string | null; onClose: () => void }) {
  const q = useApi<{ fold_decisions: Record<string, unknown>[] | null } & EvidenceRow>(row ? `/runs/${runId}/rows/${encodeURIComponent(row)}` : null);
  const folds = q.data?.fold_decisions ?? [];
  const cols = [...new Set(folds.flatMap((f) => Object.keys(f)))];   // whatever the ladder recorded per fold
  return (
    <Dialog open={row !== null} onOpenChange={(o) => !o && onClose()} title={row ?? ""} wide>
      {!folds.length ? <p className="text-sm text-muted">Fold decisions are stored for accepted rows only.</p> : (
        <Table head={cols}>
          {folds.map((f, i) => (
            <tr key={i}>
              {cols.map((k) => {
                const v = f[k];
                return <td key={k} className="max-w-72 truncate text-xs" title={typeof v === "object" ? JSON.stringify(v) : String(v ?? "")}>
                  {v === null || v === undefined ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>;
              })}
            </tr>
          ))}
        </Table>
      )}
      <p className="mt-4 text-xs text-muted">Each fold's parameters were chosen on in-sample data before its decision point. <Link to="/registry" className="text-accent">Trial registry</Link></p>
    </Dialog>
  );
}
