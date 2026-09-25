import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PageHead } from "@/components/Layout";
import { Button, ErrorNote, Field, Input, Panel } from "@/components/ui";
import { useApi, useSave } from "@/lib/api";

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
      <p className="mt-5 text-sm text-muted">The MT5 password is never stored here: set SF_MT5_PASSWORD in the environment of the machine that runs the daily job.</p>
    </>
  );
}
