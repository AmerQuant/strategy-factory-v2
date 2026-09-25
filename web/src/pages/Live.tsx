import { Link, useParams } from "react-router-dom";
import { axis, Chart } from "@/components/Chart";
import { PageHead } from "@/components/Layout";
import { Empty, FamilyDot, Panel, Signed, Table, Tabs, Tag } from "@/components/ui";
import { type LiveDetail, type LiveSummary, useApi } from "@/lib/api";
import { familyOf, money, num } from "@/lib/utils";

export function Live() {
  const l = useApi<LiveSummary[]>("/live", { refetchInterval: 30000 });
  return (
    <>
      <PageHead title="Paper & live" sub="Books kept by scripts/run_daily.py, one state file each." />
      <Panel flush>
        {l.data?.length ? (
          <Table head={["Book", "Last day", "Last decision point", "Rows", "Open positions", "Pending orders", "Closed trades", "Net P&L", "Reconciliation"]}>
            {l.data.map((b) => (
              <tr key={b.id} className="hover:bg-sunken/60">
                <td><Link to={`/live/${b.id}`} className="font-medium text-accent">{b.id}</Link></td>
                <td>{b.last_day ?? "–"}</td><td>{b.last_dp ?? "–"}</td><td>{b.rows}</td><td>{b.open_positions}</td>
                <td>{b.pending}</td><td>{b.closed_trades}</td><td><Signed value={b.net_pnl}>{money(b.net_pnl)}</Signed></td>
                <td>{b.reconciled ? <Tag tone="pos">Matches broker</Tag> : <Tag tone="neg">Differences</Tag>}</td>
              </tr>
            ))}
          </Table>
        ) : <Empty title="No books yet">Set the live folder in Platform settings and initialise a book with run_daily.py --init.</Empty>}
      </Panel>
    </>
  );
}

export function LiveDetailPage() {
  const { id = "" } = useParams();
  const q = useApi<LiveDetail>(`/live/${id}`, { refetchInterval: 30000 });
  const d = q.data;
  if (q.isError) return <Empty title="Book not found">{q.error.message}</Empty>;
  if (!d) return null;
  const last = d.equity.at(-1)?.equity ?? 0;
  return (
    <>
      <PageHead title={id} sub={<>Last day {d.last_day ?? "–"}, last decision point {d.last_dp ?? "–"}</>} />
      <Panel flush className="mb-5">
        <div className="flex items-baseline gap-3 px-5 pt-4">
          <span className="font-cond text-3xl font-semibold"><Signed value={last}>{money(last)}</Signed></span>
          <span className="text-sm text-muted">realised net P&L over {d.closed.length} closed trades</span>
        </div>
        <Chart height={240} option={(c) => ({
          grid: { left: 64, right: 20, top: 16, bottom: 36 },
          xAxis: { type: "category", data: d.equity.map((e) => e.date.slice(0, 10)), ...axis(c, { splitLine: { show: false } }) },
          yAxis: { type: "value", ...axis(c), axisLabel: { color: c.muted, formatter: (v: number) => money(v) } },
          tooltip: { trigger: "axis", valueFormatter: (v: number) => money(v) },
          series: [{ type: "line", step: "end", data: d.equity.map((e) => e.equity), showSymbol: false,
            lineStyle: { color: c.accent, width: 2 }, areaStyle: { color: c.accent, opacity: 0.08 } }],
        })} />
      </Panel>
      <Tabs tabs={[
        { value: "book", label: `Book (${d.book.length})`, content: (
          <Panel flush><Table head={["Row", "Method", "Side", "Threshold", "Exit", "Filters", "Eligible symbols"]}>
            {d.book.map((b) => (
              <tr key={b.row}>
                <td><span className="flex items-center gap-2 font-medium"><FamilyDot family={familyOf(b.row)} />{b.row}</span></td>
                <td>{b.method}</td><td>{b.direction === 1 ? "Buy" : "Sell"}</td><td>{num(b.threshold, 2)}</td>
                <td className="text-xs">{b.exit}</td><td className="text-xs">{b.filters.join(", ") || "–"}</td><td>{b.eligible}</td>
              </tr>
            ))}
          </Table></Panel>) },
        { value: "positions", label: `Open positions (${d.positions.length})`, content: (
          <Panel flush>{d.positions.length ? <Table head={["Row", "Symbol", "Quantity", "Entry date", "Entry price", "Dividends"]}>
            {d.positions.map((p) => (
              <tr key={p.row + p.symbol}><td>{p.row}</td><td className="font-medium">{p.symbol}</td><td>{num(p.qty, 2)}</td>
                <td>{String(p.entry_date).slice(0, 16)}</td><td>{num(p.entry_px)}</td><td>{num(p.dividends)}</td></tr>
            ))}
          </Table> : <Empty title="Flat">No open positions.</Empty>}</Panel>) },
        { value: "pending", label: `Pending orders (${d.pending.length})`, content: (
          <Panel flush>{d.pending.length ? <Table head={["Row", "Symbol", "Intent", "Quantity"]}>
            {d.pending.map((o, i) => (<tr key={i}><td>{o.row}</td><td className="font-medium">{o.symbol}</td><td>{o.intent}</td>
              <td><Signed value={o.qty}>{num(o.qty, 2)}</Signed></td></tr>))}
          </Table> : <Empty title="No orders for the next open" />}</Panel>) },
        { value: "rows", label: "By row", content: (
          <Panel flush><Table head={["Row", "Trades", "Win rate", "Net P&L"]}>
            {d.by_row.map((r) => (<tr key={r.row}><td><span className="flex items-center gap-2"><FamilyDot family={familyOf(r.row)} />{r.row}</span></td>
              <td>{r.trades}</td><td>{num((100 * r.wins) / Math.max(r.trades, 1), 0)}%</td>
              <td><Signed value={r.net_pnl}>{money(r.net_pnl)}</Signed></td></tr>))}
          </Table></Panel>) },
        { value: "trades", label: "Closed trades", content: (
          <Panel flush><div className="max-h-[520px] overflow-y-auto"><Table head={["Row", "Symbol", "Entry", "Exit", "Entry price", "Exit price", "Net P&L"]}>
            {[...d.closed].reverse().map((t, i) => (<tr key={i}><td className="text-xs">{t.row}</td><td className="font-medium">{t.symbol}</td>
              <td>{String(t.entry_date).slice(0, 16)}</td><td>{String(t.exit_date).slice(0, 16)}</td><td>{num(t.entry_px)}</td>
              <td>{num(t.exit_px)}</td><td><Signed value={t.net_pnl}>{money(t.net_pnl)}</Signed></td></tr>))}
          </Table></div></Panel>) },
        { value: "log", label: "Daily log", content: (
          <Panel flush><div className="max-h-[520px] overflow-y-auto"><Table head={["Day", "Orders", "Open positions", "Reconciled"]}>
            {[...d.log].reverse().map((l, i) => (<tr key={i}><td>{l.day}</td><td>{l.orders}</td><td>{l.open_positions}</td>
              <td>{l.reconciled === false ? <Tag tone="neg">no</Tag> : <Tag tone="pos">yes</Tag>}</td></tr>))}
          </Table></div></Panel>) },
      ]} />
    </>
  );
}
