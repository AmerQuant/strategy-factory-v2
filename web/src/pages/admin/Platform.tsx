import { Save, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PageHead } from "@/components/Layout";
import { Button, ErrorNote, Field, Input, Panel, Select, Tag } from "@/components/ui";
import { api, useApi, useSave } from "@/lib/api";
import { when } from "@/lib/utils";

type Settings = Record<string, string | number>;

const GROUPS: { title: string; fields: [string, string, string?, ("number")?][] }[] = [
  { title: "Data and outputs", fields: [
    ["store_path", "Data store", "The v1 store root (SFAC_DATA_ROOT) with catalog.parquet"],
    ["runs_root", "Research runs folder", "Each run_real --out folder inside it appears under Research runs"],
    ["registry_path", "Trial registry", "One DuckDB file shared by every run, so DSR counts all trials"],
    ["live_dir", "Paper & live folder", "State files written by run_daily.py"],
    ["membership_path", "Index membership", "Optional parquet: symbol, start, end"],
    ["dividends_path", "Dividends", "Optional parquet: symbol, ex_date, amount"],
  ] },
  { title: "Execution", fields: [
    ["scripts_dir", "Scripts folder", "Leave empty to use the repository's scripts/ folder"],
    ["python", "Python interpreter", "For example the uv environment's python.exe"],
    ["costs", "Default costs", "moneta, flat5, or a per-symbol cost CSV"],
    ["workers", "Workers", "Processes for the trade-cache precompute (0 = all cores)", "number"],
    ["capital", "Capital", "Account capital used for sizing and risk budgets", "number"],
    ["timezone", "Market time zone", "Used for the broker clock and schedules"],
  ] },
];

export function Platform() {
  const s = useApi<Settings>("/settings");
  const [form, setForm] = useState<Settings>({});
  const save = useSave<Settings>(["/settings", "/runs", "/live", "/registry"]);
  useEffect(() => { if (s.data) setForm(s.data); }, [s.data]);
  const set = (k: string, v: string, n?: boolean) => setForm((f) => ({ ...f, [k]: n ? Number(v) : v }));
  const submit = () => save.mutate({ method: "put", path: "/settings", body: form }, {
    onSuccess: () => toast.success("Platform settings saved"), onError: (e) => toast.error(e.message),
  });
  return (
    <>
      <PageHead title="Platform" sub="Where the platform reads data and writes results. Paths are on the machine running the server."
        action={<Button variant="primary" onClick={submit} disabled={save.isPending}><Save className="size-4" />Save settings</Button>} />
      <ErrorNote error={save.error} />
      <div className="grid gap-5 lg:grid-cols-2">
        {GROUPS.map((g) => (
          <Panel key={g.title} title={g.title}>
            <div className="grid gap-4">
              {g.fields.map(([k, label, hint, type]) => (
                <Field key={k} label={label} hint={hint}>
                  <Input type={type ?? "text"} value={String(form[k] ?? "")} onChange={(e) => set(k, e.target.value, type === "number")} />
                </Field>
              ))}
            </div>
          </Panel>
        ))}
      </div>
      <Telegram form={form} set={set} />
      <p className="mt-5 text-sm text-muted">The MT5 password is never stored here: set SF_MT5_PASSWORD in the environment of the machine that runs the daily job.</p>
    </>
  );
}

interface NotifyStatus { token_set: boolean; chat_set: boolean; min_level: string; last_sent_at: string | null;
  sent_total: number | null; last_error: string | null; last_error_at: string | null }

function Telegram({ form, set }: { form: Settings; set: (k: string, v: string) => void }) {
  const st = useApi<NotifyStatus>("/notify", { refetchInterval: 20000 });
  const [busy, setBusy] = useState(false);
  const test = async () => {
    setBusy(true);
    try { await api.post("/notify/test"); toast.success("Test message sent"); }
    catch (e) { toast.error((e as Error).message); }
    finally { setBusy(false); }
  };
  const s = st.data;
  return (
    <Panel className="mt-5" title="Notifications (Telegram)" action={
      <Button size="sm" onClick={test} disabled={busy}><Send className="size-3.5" />Send test message</Button>}>
      <div className="grid gap-4 lg:grid-cols-4">
        <Field label="Chat id" hint="Your user or group id; save the settings after changing it">
          <Input value={String(form.telegram_chat_id ?? "")} onChange={(e) => set("telegram_chat_id", e.target.value)} /></Field>
        <Field label="Send from level">
          <Select value={String(form.telegram_min_level ?? "warning")} onChange={(e) => set("telegram_min_level", e.target.value)}>
            <option value="critical">Critical only</option><option value="warning">Warning and critical</option><option value="info">Everything</option>
          </Select></Field>
        <Field label="HTTP proxy" hint="e.g. http://127.0.0.1:10809 when Telegram needs one">
          <Input value={String(form.telegram_proxy ?? "")} onChange={(e) => set("telegram_proxy", e.target.value)} /></Field>
        <div className="grid content-start gap-1.5 text-sm">
          <span className="font-medium">Status</span>
          <div className="flex flex-wrap gap-1.5">
            {s?.token_set ? <Tag tone="pos">token set</Tag> : <Tag tone="warn">SF_TELEGRAM_TOKEN missing</Tag>}
            {s?.chat_set ? <Tag tone="pos">chat set</Tag> : <Tag tone="warn">no chat id</Tag>}
          </div>
          <span className="text-xs text-muted">{s?.last_sent_at ? `Last sent ${when(s.last_sent_at)} (${s.sent_total ?? 0} in total)` : "Nothing sent yet"}</span>
          {s?.last_error && <span className="text-xs text-neg">{s.last_error}</span>}
        </div>
      </div>
      <p className="mt-3 text-xs text-muted">The scheduler service sends new events every tick. The bot token is read from SF_TELEGRAM_TOKEN
        on the machine that runs the scheduler (and the dashboard, for the test button); it is never stored.</p>
    </Panel>
  );
}
