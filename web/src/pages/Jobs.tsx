import { Play, Square } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { PageHead } from "@/components/Layout";
import { Button, Dialog, Empty, Panel, Select, Table } from "@/components/ui";
import { type Job, useApi, useSave } from "@/lib/api";
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
      <Panel flush>
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
