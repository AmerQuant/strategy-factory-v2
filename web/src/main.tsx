import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthGate } from "@/components/AuthGate";
import { Layout } from "@/components/Layout";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { Collection } from "@/pages/admin/Collection";
import { Platform } from "@/pages/admin/Platform";
import { Jobs } from "@/pages/Jobs";
import { Live, LiveDetailPage } from "@/pages/Live";
import { Overview } from "@/pages/Overview";
import { Registry } from "@/pages/Registry";
import { RunDetail } from "@/pages/RunDetail";
import { Runs } from "@/pages/Runs";
import "./index.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false } } });

const router = createBrowserRouter([
  { path: "/", element: <Layout />, children: [
    { index: true, element: <Overview /> },
    { path: "runs", element: <Runs /> },
    { path: "runs/:id", element: <RunDetail /> },
    { path: "registry", element: <Registry /> },
    { path: "live", element: <Live /> },
    { path: "live/:id", element: <LiveDetailPage /> },
    { path: "jobs", element: <Jobs /> },
    { path: "admin/platform", element: <Platform /> },
    { path: "admin/:collection", element: <Collection /> },
  ] },
]);

function Themed() {
  const { theme } = useTheme();
  return <Toaster theme={theme} position="bottom-right" richColors />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <AuthGate><RouterProvider router={router} /></AuthGate>
        <Themed />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
