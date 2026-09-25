import { useState } from "react";
import { axis, Chart } from "@/components/Chart";
import { PageHead } from "@/components/Layout";
import { Empty, FamilyDot, Input, Panel, Table } from "@/components/ui";
import { useApi } from "@/lib/api";
import { familyOf, num, when } from "@/lib/utils";

interface Reg {
  available: boolean; trials: number;
  recent: { trial_id: string; row_id: string; data_version: string; code_version: string; created_at: string;
    metrics: Record<string, number> }[];
  by_row: { row: string; n: number }[]; by_data_version?: { data_version: string; n: number }[];
}

export function Registry() {
  const r = useApi<Reg>("/registry");
  const [q, setQ] = useState("");
  if (r.data && !r.data.available) {
    return (<><PageHead title="Trial registry" /><Panel><Empty title="No registry file">Set the registry path in Platform settings. Every configuration a run evaluates is recorded there, and the deflated Sharpe counts all of them.</Empty></Panel></>);
  }
  const top = (r.data?.by_row ?? []).slice(0, 25);
  const recent = (r.data?.recent ?? []).filter((t) => !q || t.row_id.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <PageHead title="Trial registry" sub={<>{r.data?.trials ?? "–"} trials recorded. Each one counts in the multiple-testing correction.</>} />
      <div className="grid gap-5 lg:grid-cols-5">
        <Panel title="Trials per row" className="lg:col-span-2">
          <Chart height={Math.max(240, top.length * 18)} option={(c) => ({
            grid: { left: 190, right: 20, top: 6, bottom: 24 }, tooltip: { trigger: "item" },
            xAxis: { type: "value", ...axis(c), minInterval: 1 },
            yAxis: { type: "category", inverse: true, data: top.map((t) => t.row), ...axis(c, { splitLine: { show: false } }),
              axisLabel: { color: c.muted, fontSize: 11 } },
            series: [{ type: "bar", barMaxWidth: 12, data: top.map((t) => ({ value: t.n, itemStyle: { color: c.fam[familyOf(t.row)] } })) }],
          })} />
        </Panel>
        <Panel title="Recent trials" className="lg:col-span-3" flush action={<Input className="h-7 w-52 text-xs" placeholder="Filter by row" value={q} onChange={(e) => setQ(e.target.value)} />}>
          <div className="max-h-[560px] overflow-y-auto">
            <Table head={["Row", "Recorded", "Data version", "Sharpe", "Trades"]}>
              {recent.map((t) => (
                <tr key={t.trial_id}>
                  <td><span className="flex items-center gap-2"><FamilyDot family={familyOf(t.row_id)} />{t.row_id}</span></td>
                  <td className="whitespace-nowrap text-muted">{when(t.created_at)}</td>
                  <td className="max-w-48 truncate text-xs text-muted" title={t.data_version}>{t.data_version}</td>
                  <td>{num(t.metrics?.sharpe)}</td>
                  <td>{t.metrics?.n ?? "–"}</td>
                </tr>
              ))}
            </Table>
          </div>
        </Panel>
      </div>
    </>
  );
}
