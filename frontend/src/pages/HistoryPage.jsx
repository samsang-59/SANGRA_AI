import { Link, useNavigate, useSearchParams } from "react-router-dom";
import EmptyState from "../components/EmptyState.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import { Notice, Spinner } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { RUNS_PER_PAGE, useProjects, useRuns } from "../hooks/queries.js";
import { ALL_STATUSES, formatDate } from "../runStatus.js";
import { readFilters } from "../runRules.js";

export default function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const filters = readFilters(params);
  const runs = useRuns(filters);
  const projects = useProjects();

  function change(next) {
    const merged = { ...filters, ...next };
    const query = {};
    if (merged.projectId) query.project = String(merged.projectId);
    if (merged.status) query.status = merged.status;
    if (merged.page > 1) query.page = String(merged.page);
    setParams(query);
  }

  const total = runs.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / RUNS_PER_PAGE));
  const items = runs.data?.items ?? [];
  const filtered = Boolean(filters.projectId || filters.status);

  return (
    <main className="page">
      <div className="page-header history-header">
        <h1>History</h1>
        <div className="filters">
          <label className="filter">
            <span className="filter-label mono">project</span>
            <select className="input select" value={filters.projectId ?? ""}
              onChange={(e) => change({ projectId: e.target.value ? Number(e.target.value) : null, page: 1 })}>
              <option value="">all projects</option>
              {(projects.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="filter">
            <span className="filter-label mono">status</span>
            <select className="input select" value={filters.status ?? ""}
              onChange={(e) => change({ status: e.target.value || null, page: 1 })}>
              <option value="">all statuses</option>
              <option value="active">active</option>
              {ALL_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>
      </div>

      {runs.isPending && <div className="center-block"><Spinner label="Loading runs" /></div>}
      {runs.isError && (
        <Notice kind="error" action={<button type="button" className="notice-action" onClick={() => runs.refetch()}>retry</button>}>
          {messageFor(runs.error)}
        </Notice>
      )}
      {runs.isSuccess && items.length === 0 && (
        filtered || filters.page > 1
          ? <EmptyState title={filters.page > 1 ? "There are no runs on this page" : "No runs match these filters"}
            symbol="[ ]" action={<Link className="button" to="/runs">show all runs</Link>} />
          : <EmptyState title="No runs yet - start one from a project" symbol="[ ]"
            action={<Link className="button" to="/projects">start a run</Link>} />
      )}
      {runs.isSuccess && items.length > 0 && (
        <>
          <div className="table-card card">
            <table className={`table runs-table${runs.isPlaceholderData ? " table-loading" : ""}`}>
              <thead>
                <tr>
                  <th scope="col">run</th>
                  <th scope="col">project</th>
                  <th scope="col">status</th>
                  <th scope="col">date</th>
                  <th scope="col"><span className="visually-hidden">open</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((run) => (
                  <tr key={run.id} className="row-link" onClick={() => navigate(`/runs/${run.id}`)}>
                    <td className="mono">#{run.id}</td>
                    <td><span className="clip" title={run.project_name}>{run.project_name}</span></td>
                    <td><StatusBadge status={run.status} /></td>
                    <td className="cell-muted">{formatDate(run.created_at)}</td>
                    <td className="cell-open">
                      <Link to={`/runs/${run.id}`} onClick={(e) => e.stopPropagation()}
                        aria-label={`open run ${run.id}`}>open {"→"}</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row-between pager">
            <span className="mono label-muted">{RUNS_PER_PAGE} per page {"·"} page {filters.page} of {pages}</span>
            <div className="row">
              <button type="button" className="button button-small" disabled={filters.page <= 1}
                onClick={() => change({ page: filters.page - 1 })}>{"←"} prev</button>
              <button type="button" className="button button-small" disabled={filters.page >= pages}
                onClick={() => change({ page: filters.page + 1 })}>next {"→"}</button>
            </div>
          </div>
        </>
      )}
    </main>
  );
}
