import { QueryClient } from "@tanstack/react-query";

// One QueryClient for the app (F4). Retries and refetch rules are set per hook in the later phases.
export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
}
