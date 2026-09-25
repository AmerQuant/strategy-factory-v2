import { Link } from "react-router-dom";
import { axis, Chart } from "@/components/Chart";
import { PageHead } from "@/components/Layout";
import { Empty, FamilyDot, Panel, Signed, Tag } from "@/components/ui";
import { type Evidence, type Job, type LiveSummary, type RunSummary, useApi } from "@/lib/api";
import { familyOf, FAMILIES, money, num, when } from "@/lib/utils";

export function Overview() {
  const runs = useApi<RunSummary[]>("/runs");
  const latest = runs.data?.[0];
  const ev = useApi<Evidence>(latest ? `/runs/${latest.id}` : null);
  const live = useApi<LiveSummary[]>("/live", { refetchInterval: 30000 });
  const jobs = useApi<Job[]>("/jobs", { refetchInterval: 10000 });

  if (runs.isSuccess && !latest) {
    return (
      <>
        <PageHead title="Overview" />
        <Panel>
          <Empty title="No research runs yet">
            Set the runs folder in <Link className="text-accent underline" to="/admin/platform">Platform</Link>, then
            start a research run from <Link className="text-accent underline" to="/jobs">Jobs</Link>. Every folder with an
            evidence.json appears here.
          </Empty>
        </Panel>
      </>
    );
  }

  const accepted = (ev.data?.rows ?? []).filter((r) => r.path === "standalone" || r.path === "portfolio");
  const byFam = Object.entries(accepted.reduce<Record<string, number>>((a, r) => {
    const f = familyOf(r.row); a[f] = (a[f] ?? 0) + 1; return a;
  }, {}));
  const curve = ev.data?.dev_curve;

  return (
    <>
      <PageHead title="Overview" sub={latest && <>Latest run <Link to={`/runs/${latest.id}`} className="text-accent">{latest.id}</Link>, {when(latest.generated_at)}</>} />
      <Panel flush className="mb-5 overflow-hidden">
        <div className="flex flex-wrap items-baseline justify-between gap-4 px-5 pt-4">
          <div>
            <h2 className="text-sm text-muted">Combined policy, out-of-sample (development period)</h2>
            <p className="font-cond text-4xl font-semibold tracking-tight">
              <Signed value={ev.data?.combined_dev.sharpe}>{num(ev.data?.combined_dev.sharpe)}</Signed>
              <span className="ml-2 text-base font-medium text-muted">Sharpe</span>
            </p>
          </div>
          <div className="text-right text-sm text-muted">
            Benchmark, all rows equal weight: <span className="text-ink">{num(ev.data?.benchmark_all_rows_equal_dev)}</span>
          </div>
        </div>
        {curve ? (
          <Chart height={300} option={(c) => ({
            legend: { top: 0, right: 16, textStyle: { color: c.muted } },
            grid: { left: 64, right: 24, top: 36, bottom: 40 },
            xAxis: { type: "category", data: curve.dates, ...axis(c, { splitLine: { show: false } }) },
            yAxis: { type: "value", ...axis(c), axisLabel: { color: c.muted, formatter: (v: number) => money(v) } },
            tooltip: { trigger: "axis", valueFormatter: (v: number) => money(v) },
            dataZoom: [{ type: "inside" }],
            series: [
              { name: "Combined policy", type: "line", data: curve.combined, showSymbol: false, lineStyle: { width: 2.2, color: c.accent },
                areaStyle: { color: c.accent, opacity: 0.08 }, itemStyle: { color: c.accent } },
              { name: "All rows, equal weight", type: "line", data: curve.benchmark, showSymbol: false,
                lineStyle: { width: 1.4, type: "dashed", color: c.muted }, itemStyle: { color: c.muted } },
            ],
          })} />
        ) : <div className="h-[300px]" />}
        <dl className="grid grid-cols-2 border-t border-line text-sm sm:grid-cols-3 lg:grid-cols-6 [&>div]:px-5 [&>div]:py-3 [&>div]:border-line [&>div:not(:last-child)]:border-r">
          <div><dt className="text-muted">Accepted rows</dt><dd className="font-cond text-xl font-semibold">{accepted.length} <span className="text-sm font-normal text-muted">of {ev.data?.rows.length ?? "–"}</span></dd></div>
          <div><dt className="text-muted">Effective bets</dt><dd className="font-cond text-xl font-semibold">{num(ev.data?.combined_dev.effective_n, 1)}</dd></div>
          <div><dt className="text-muted">Trials in registry</dt><dd className="font-cond text-xl font-semibold">{ev.data?.trials_in_registry ?? "–"}</dd></div>
          <div><dt className="text-muted">SPA p, any vs cash</dt><dd className="font-cond text-xl font-semibold">{num(ev.data?.spa_any_vs_cash?.p_value, 3)}</dd></div>
          <div><dt className="text-muted">Holdout</dt><dd className="font-cond text-xl font-semibold capitalize">{String(ev.data?.holdout?.status ?? "locked")}</dd></div>
          <div><dt className="text-muted">Data</dt><dd className="truncate pt-1 text-xs" title={String(ev.data?.meta?.data ?? "")}>{String(ev.data?.meta?.data ?? "–")}</dd></div>
        </dl>
      </Panel>

      <div className="grid gap-5 lg:grid-cols-3">
        <Panel title="Accepted rows by edge family">
          {byFam.length ? (
            <ul className="grid gap-2">
              {byFam.map(([f, n]) => (
                <li key={f} className="flex items-center gap-2.5 text-sm">
                  <FamilyDot family={f} /><span className="flex-1">{FAMILIES[f]?.label ?? f}</span>
                  <span className="font-medium">{n}</span>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">No row passed the gates in this run.</p>}
        </Panel>
        <Panel title="Paper & live books" action={<Link to="/live" className="text-xs text-accent">Open</Link>}>
          {live.data?.length ? (
            <ul className="grid gap-3">
              {live.data.map((b) => (
                <li key={b.id}>
                  <Link to={`/live/${b.id}`} className="flex items-center justify-between gap-3 text-sm hover:text-accent">
                    <span className="font-medium">{b.id}</span>
                    <Signed value={b.net_pnl}>{money(b.net_pnl)}</Signed>
                  </Link>
                  <div className="mt-0.5 flex gap-2 text-xs text-muted">
                    <span>Last day {b.last_day ?? "–"}</span><span>{b.open_positions} open</span>
                    {b.reconciled ? <Tag tone="pos">Reconciled</Tag> : <Tag tone="neg">Mismatch</Tag>}
                  </div>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">No state files in the live folder.</p>}
        </Panel>
        <Panel title="Recent jobs" action={<Link to="/jobs" className="text-xs text-accent">Open</Link>}>
          {jobs.data?.length ? (
            <ul className="grid gap-2.5 text-sm">
              {jobs.data.slice(0, 6).map((j) => (
                <li key={j.id} className="flex items-center justify-between gap-2">
                  <span className="truncate">{j.preset || j.kind}</span>
                  <JobStatus status={j.status} />
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">Nothing has been started from this admin yet.</p>}
        </Panel>
      </div>
    </>
  );
}

export function JobStatus({ status }: { status: string }) {
  const tone = status === "succeeded" ? "pos" : status === "failed" ? "neg" : status === "running" ? "accent" : "neutral";
  return <Tag tone={tone}>{status}</Tag>;
}
