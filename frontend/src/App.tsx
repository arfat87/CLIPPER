import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AdminShell } from "./components/AdminShell";
import { StudioShell } from "./components/StudioShell";
import { ToastProvider } from "./components/ui";
import { useSyncRoute } from "./router";
import { useStudioStore } from "./store/studioStore";

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
  return area === "admin" ? <AdminShell /> : <StudioShell />;
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
