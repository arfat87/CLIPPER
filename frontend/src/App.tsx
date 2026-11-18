import { lazy, Suspense } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LoadingState } from "./components/StateViews";
import { StudioShell } from "./components/StudioShell";
import { ToastProvider } from "./components/ui";
import { useSyncRoute } from "./router";
import { useStudioStore } from "./store/studioStore";

const AdminShell = lazy(() => import("./components/AdminShell"));

const defaultQueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
    },
  },
});

function AppRouterView() {
  useSyncRoute();
  const area = useStudioStore((s) => s.area);
  if (area === "admin") {
    return (
      <Suspense
        fallback={
          <div className="min-h-screen bg-bg p-6">
            <LoadingState />
          </div>
        }
      >
        <AdminShell />
      </Suspense>
    );
  }
  return <StudioShell />;
}

export function App({ client = defaultQueryClient }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AppRouterView />
      </ToastProvider>
    </QueryClientProvider>
  );
}

export default App;
