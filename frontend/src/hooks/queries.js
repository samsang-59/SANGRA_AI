// Data hooks (05_frontend_design.md F4): small wrappers of the API functions with TanStack Query.
// Keys: ["projects"], ["project", id], ["runs", filters, page], ["run", id], ["activeRun"].
import { useQuery } from "@tanstack/react-query";
import { getProject, listProjects } from "../api/projects.js";
import { getRun, listRuns } from "../api/runs.js";
import { useAuth } from "../auth/useAuth.js";

export const keys = {
  projects: ["projects"],
  project: (id) => ["project", String(id)],
  activeRun: ["activeRun"],
  run: (id) => ["run", String(id)],
};

export function useProjects() {
  return useQuery({ queryKey: keys.projects, queryFn: listProjects });
}

/** One project; projectId null = no request (the "new project" form). */
export function useProject(projectId) {
  return useQuery({ queryKey: keys.project(projectId), queryFn: () => getProject(projectId),
    enabled: projectId !== null && projectId !== undefined });
}

/** The user's active run, or null (only one can exist). Refreshed every 30 seconds while logged in (F4). */
export function useActiveRun() {
  const { status } = useAuth();
  return useQuery({
    queryKey: keys.activeRun,
    queryFn: async () => (await listRuns({ status: "active", limit: 1 })).items[0] ?? null,
    enabled: status === "loggedIn",
    refetchInterval: 30_000,
  });
}

/** One page of the history (key ["runs", filters, page], F4). */
export function useRuns({ projectId, status, page }) {
  return useQuery({
    queryKey: ["runs", { projectId: projectId ?? null, status: status ?? null }, page],
    queryFn: () => listRuns({ projectId, status, page, limit: RUNS_PER_PAGE }),
    placeholderData: (previous) => previous, // the old page stays while the next one loads
  });
}

export const RUNS_PER_PAGE = 20;

/** One run with its attempts, edits, test results and package (F6). */
export function useRun(runId) {
  return useQuery({ queryKey: keys.run(runId), queryFn: () => getRun(runId) });
}
