import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { api, type Health, useApi } from "@/lib/api";

/** Shows the sign-in screen when the server requires a token and this browser has no session. */
export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const health = useApi<Health>("/health");
  useEffect(() => {
    const onUnauthorized = () => qc.invalidateQueries({ queryKey: ["/health"] });
    window.addEventListener("sf-unauthorized", onUnauthorized);
    return () => window.removeEventListener("sf-unauthorized", onUnauthorized);
  }, [qc]);
  if (health.isLoading) return null;
  if (health.data?.auth_required && !health.data.authenticated) return <SignIn />;
  return <>{children}</>;
}

function SignIn() {
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/login", { token });
      await qc.invalidateQueries();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid min-h-full place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-line bg-surface p-6">
        <div className="mb-5 flex items-center gap-2.5">
          <KeyRound className="size-5 text-accent" />
          <div>
            <h1 className="font-cond text-lg font-semibold">Strategy Factory</h1>
            <p className="text-sm text-muted">This server requires its access token.</p>
          </div>
        </div>
        <div className="grid gap-4">
          <Field label="Access token" hint="The value of SF_WEB_TOKEN (or the token file) on the server.">
            <Input type="password" autoComplete="current-password" autoFocus value={token} onChange={(e) => setToken(e.target.value)} />
          </Field>
          <ErrorNote error={error} />
          <Button variant="primary" type="submit" disabled={!token || busy} className="justify-center">Sign in</Button>
        </div>
      </form>
    </div>
  );
}
