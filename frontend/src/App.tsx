import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StudioShell } from "./components/StudioShell";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <StudioShell />
    </QueryClientProvider>
  );
}

export default App;
