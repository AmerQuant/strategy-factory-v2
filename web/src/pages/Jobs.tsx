import { Play, Square } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { PageHead } from "@/components/Layout";
import { Button, Dialog, Empty, Panel, Select, Table, Tag } from "@/components/ui";
import { type Job, type SchedulerSummary, useApi, useSave } from "@/lib/api";
import { when } from "@/lib/utils";
import { JobStatus } from "./Overview";

export function Jobs() {
  const jobs = useApi<Job[]>("/jobs", { refetchInterval: 4000 });
  const presets = useApi<{ id: string; name: string; kind: string }[]>("/config/job_presets");
  const [preset, setPreset] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const launch = useSave<Job>(["/jobs"]);
  const start = () => launch.mutate({ method: "post", path: "/jobs", body: { preset } }, {
    onSuccess: (j) => { toast.success(`Started ${j.preset || j.kind}`); setOpen(j.id); },
    onError: (e) => toast.error(e.message),
  });
  return (
    <>
      <PageHead title="Jobs" sub="Research runs, the daily paper job and utilities, started from saved presets." action={
        <div className="flex gap-2">
          <Select className="w-64" value={preset} onChange={(e) => setPreset(e.target.value)}>
            <option value="">Choose a preset</option>
            {presets.data?.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.kind})</option>)}
          </Select>
          <Button variant="primary" disabled={!preset || launch.isPending} onClick={start}><Play className="size-4" />Start job</Button>
        </div>} />
      <SchedulerPanel onOpen={setOpen} />
      <Panel flush title="Job runs">
        {jobs.data?.length ? (
          <Table head={["Job", "Preset", "Kind", "Started", "Finished", "Status", ""]}>
            {jobs.data.map((j) => (
              <tr key={j.id}>
                <td><button className="font-medium text-accent" onClick={() => setOpen(j.id)}>{j.id}</button></td>
                <td>{j.preset || "–"}</td><td>{j.kind}</td><td className="text-muted">{when(j.started_at)}</td>
                <td className="text-muted">{when(j.finished_at)}</td><td><JobStatus status={j.status} /></td>
                <td className="text-right"><Button size="sm" variant="ghost" onClick={() => setOpen(j.id)}>View log</Button></td>
              </tr>
            ))}
          </Table>
        ) : <Empty title="No jobs yet">Create a preset in <Link to="/admin/job_presets" className="text-accent underline">Job presets</Link>, then start it here.</Empty>}
      </Panel>
      <JobDialog id={open} onClose={() => setOpen(null)} />
    </>
  );
}

function RunStatus({ status }: { status: string }) {
  const tone = status === "succeeded" ? "pos" : status === "failed" || status === "missed" ? "neg"
    : status === "running" ? "accent" : status === "skipped" ? "warn" : "neutral";
  return <Tag tone={tone}>{status}</Tag>;
}

function SchedulerPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const q = useApi<SchedulerSummary>("/scheduler", { refetchInterval: 10000 });
  const d = q.data;
  if (!d) return null;
  const head = d.alive
    ? <Tag tone="pos">service running</Tag>
    : <Tag tone="neg">{d.heartbeat ? "service not running" : "service never started"}</Tag>;
  return (
    <Panel flush className="mb-5" title={<span className="flex items-center gap-2">Scheduler {head}</span>}
      action={<span className="text-xs text-muted">{d.heartbeat ? `last tick ${when(d.heartbeat.at)}` : "python -m sfactory.scheduler --config <dir>"}</span>}>
      {d.schedules.length ? (
        <Table head={["Schedule", "Steps", "Next run", "Last run", ""]}>
          {d.schedules.map((s) => (
            <tr key={s.id}>
              <td className="font-medium">{s.name}{!s.enabled && <span className="ml-2"><Tag>paused</Tag></span>}
                {s.error && <div className="text-xs text-neg">{s.error}</div>}</td>
              <td className="text-xs">{s.steps.join(" \u2192 ")}</td>
              <td className="text-muted">{s.next ? when(s.next) : "\u2013"}</td>
              <td>{s.last ? <span className="flex items-center gap-2"><RunStatus status={s.last.status} />
                <span className="text-xs text-muted">{when(s.last.started_at)}</span></span> : <span className="text-muted">never</span>}
                {s.last?.note && <div className="text-xs text-muted">{s.last.note}</div>}</td>
              <td className="text-right">{s.last?.steps.map((st) => (
                <Button key={st.job} size="sm" variant="ghost" onClick={() => onOpen(st.job)} title={st.preset}>{st.preset}</Button>))}</td>
            </tr>
          ))}
        </Table>
      ) : <Empty title="No schedules">Create one in Admin, Schedules; the scheduler service runs it.</Empty>}
    </Panel>
  );
}

function JobDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const j = useApi<Job>(id ? `/jobs/${id}` : null, { refetchInterval: 2000 });
  const cancel = useSave<Job>(["/jobs"]);
  return (
    <Dialog open={id !== null} onOpenChange={(o) => !o && onClose()} title={id ?? ""} wide>
      {j.data && (
        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <code className="truncate text-xs text-muted">{j.data.cmd.join(" ")}</code>
            <div className="flex items-center gap-2">
              <JobStatus status={j.data.status} />
              {j.data.status === "running" && (
                <Button size="sm" variant="danger" onClick={() => cancel.mutate({ method: "post", path: `/jobs/${id}/cancel` })}>
                  <Square className="size-3.5" />Stop
                </Button>)}
            </div>
          </div>
          <pre className="max-h-[60vh] overflow-auto rounded-md bg-sunken p-3 text-xs leading-relaxed">{(j.data.log ?? []).join("\n") || "No output yet."}</pre>
        </div>
      )}
    </Dialog>
  );
}
