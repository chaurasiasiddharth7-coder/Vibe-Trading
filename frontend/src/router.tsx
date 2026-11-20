import { Suspense, lazy, useEffect, type ComponentType } from "react";
import { Navigate, createBrowserRouter, useRouteError } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DASHBOARD_ONLY } from "@/lib/hosted";
import { DeskShell, Landing } from "@/components/shell/DeskShell";
import { SessionProvider } from "@/lib/session";

const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Runtime = lazy(() =>
  import("@/pages/Runtime").then((m) => ({ default: m.Runtime })),
);
const Reports = lazy(() =>
  import("@/pages/Reports").then((m) => ({ default: m.Reports })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);
const Dashboard = lazy(() => import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const Intraday = lazy(() => import("@/pages/Intraday").then((m) => ({ default: m.Intraday })));
const Btst = lazy(() => import("@/pages/Btst").then((m) => ({ default: m.Btst })));
const Portfolio = lazy(() => import("@/pages/Portfolio").then((m) => ({ default: m.Portfolio })));
const Fno = lazy(() => import("@/pages/Fno").then((m) => ({ default: m.Fno })));
const Wealth = lazy(() => import("@/pages/Wealth").then((m) => ({ default: m.Wealth })));
const Suggestions = lazy(() => import("@/pages/Suggestions").then((m) => ({ default: m.Suggestions })));
const Strategies = lazy(() => import("@/pages/Strategies").then((m) => ({ default: m.Strategies })));
const UsersPage = lazy(() => import("@/pages/Users").then((m) => ({ default: m.UsersPage })));
const MyLayout = lazy(() => import("@/pages/MyLayout").then((m) => ({ default: m.MyLayout })));

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}


/**
 * A browser tab left open across a deploy still points at old script files that no longer exist.
 * Reload once to pick up the new build; if it keeps failing, show a plain message.
 */
function RouteError() {
  const error = useRouteError();
  const message = error instanceof Error ? error.message : String(error ?? "");
  const stale = /dynamically imported module|Importing a module script failed|Loading chunk/i.test(message);
  useEffect(() => {
    if (!stale) return;
    try {
      if (sessionStorage.getItem("chunk-reload") !== "1") {
        sessionStorage.setItem("chunk-reload", "1");
        window.location.reload();
        return;
      }
      sessionStorage.removeItem("chunk-reload");
    } catch {
      // storage unavailable: show the message below
    }
  }, [stale]);
  return (
    <main className="min-h-screen grid place-items-center p-6 text-center">
      <div className="space-y-3 max-w-md">
        <h1 className="text-xl font-semibold">{stale ? "A new version is available" : "Something went wrong"}</h1>
        <p className="text-sm text-muted-foreground">
          {stale ? "Reload the page to get the latest version." : "Try reloading. If it keeps happening, go back to the dashboard."}
        </p>
        <div className="flex justify-center gap-2">
          <button type="button" onClick={() => window.location.reload()} className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold">
            Reload
          </button>
          <a href="/dashboard" className="min-h-10 px-4 inline-flex items-center rounded-md border border-border text-sm">
            Dashboard
          </a>
        </div>
      </div>
    </main>
  );
}

const dashboardRoutes = [
  { path: "/dashboard", element: wrap(Dashboard) },
  { path: "/intraday", element: wrap(Intraday) },
  { path: "/btst", element: wrap(Btst) },
  { path: "/portfolio", element: wrap(Portfolio) },
  { path: "/fno", element: wrap(Fno) },
  { path: "/wealth", element: wrap(Wealth) },
  { path: "/suggestions", element: wrap(Suggestions) },
  { path: "/strategies", element: wrap(Strategies) },
  { path: "/users", element: wrap(UsersPage) },
  { path: "/layout", element: wrap(MyLayout) },
  { path: "/guide", element: <Navigate to="/dashboard" replace /> },
  { path: "/desk", element: <Navigate to="/dashboard?tab=chart" replace /> },
];

// The hosted build only has a backend for the dashboard, so every other address lands there.
const appRoutes = DASHBOARD_ONLY
  ? [{ path: "/", element: <Landing /> }, ...dashboardRoutes, { path: "*", element: <Landing /> }]
  : [
      { path: "/", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/reports", element: wrap(Reports) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      ...dashboardRoutes,
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
    ];

export const router = createBrowserRouter([
  {
    // Hosted: the Kite-style top-menu shell with password sign-in. Local: the full app's sidebar.
    element: DASHBOARD_ONLY ? (
      <DeskShell />
    ) : (
      <SessionProvider>
        <Layout />
      </SessionProvider>
    ),
    errorElement: <RouteError />,
    children: appRoutes,
  },
]);
