import { Link } from "react-router-dom";
import { PageHead } from "@/components/Layout";
import { Empty, Panel, Signed, Table, Tag } from "@/components/ui";
import { type RunSummary, useApi } from "@/lib/api";
import { num, when } from "@/lib/utils";

export function Runs() {
  const runs = useApi<RunSummary[]>("/runs");
  return (
    <>
      <PageHead title="Research runs" sub="One row per output folder of run_real (evidence.json)." />
      <Panel flush>
        {runs.data?.length ? (
          <Table head={["Run", "Generated", "Timeframe", "Catalogue", "Accepted", "Trials", "Combined Sharpe", "Effective bets", "Holdout"]}>
            {runs.data.map((r) => (
              <tr key={r.id} className="hover:bg-sunken/60">
                <td><Link to={`/runs/${r.id}`} className="font-medium text-accent">{r.id}</Link></td>
                <td className="whitespace-nowrap text-muted">{when(r.generated_at)}</td>
                <td>{r.timeframe ?? "–"}</td>
                <td className="text-muted">{r.catalog ?? "–"}</td>
                <td>{r.accepted} / {r.rows}</td>
                <td>{r.trials ?? "–"}</td>
                <td><Signed value={r.combined_sharpe}>{num(r.combined_sharpe)}</Signed></td>
                <td>{num(r.effective_n, 1)}</td>
                <td>{r.holdout ? <Tag tone={r.holdout === "passed" ? "pos" : "warn"}>{r.holdout}</Tag> : <Tag>locked</Tag>}</td>
              </tr>
            ))}
          </Table>
        ) : <Empty title={runs.isLoading ? "Loading runs" : "No runs found"}>Check the runs folder in Platform settings.</Empty>}
      </Panel>
    </>
  );
}
