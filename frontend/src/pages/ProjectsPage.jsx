import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { deleteProject } from "../api/projects.js";
import { useAuth } from "../auth/useAuth.js";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { Notice, Spinner } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { keys, useActiveRun, useProjects } from "../hooks/queries.js";
import { ACTIVE_RUN_REASON, VERIFY_REASON } from "../runRules.js";


function Source({ project }) {
  if (project.source_type === "zip") {
    return (
      <div className="source-cell">
        <span className="source-type">zip</span>
        <span className="source-ref">{"\u2014"} (zip uploaded per run)</span>
      </div>
    );
  }
  return (
    <div className="source-cell">
      <span className="source-type">{project.source_type}</span>
      <span className="source-ref" title={project.source_reference}>{project.source_reference}</span>
    </div>
  );
}

function ProjectRow({ project, runBlockedBy, onDelete }) {
  return (
    <tr>
      <td className="project-name"><span className="clip" title={project.name}>{project.name}</span></td>
      <td><span className="tag">{project.language}</span></td>
      <td className="cell-source"><Source project={project} /></td>
      <td className="mono cell-muted"><span className="clip" title={project.branch || "default"}>{project.branch || "default"}</span></td>
      <td className="mono"><span className="clip" title={project.test_command}>{project.test_command}</span></td>
      <td>
        <div className="row-actions">
          {runBlockedBy && <span className="run-reason">{runBlockedBy}</span>}
          {runBlockedBy ? (
            <button type="button" className="button button-small" disabled aria-label={`run ${project.name}`}
              title={runBlockedBy}>{"\u25b6"} run</button>
          ) : (
            <Link className="button button-primary button-small" to={`/projects/${project.id}/run`}
              aria-label={`run ${project.name}`}>{"\u25b6"} run</Link>
          )}
          <Link className="button button-small" to={`/projects/${project.id}/edit`} aria-label={`edit ${project.name}`}>
            edit
          </Link>
          <button type="button" className="button button-danger button-small" onClick={(e) => onDelete(project, e)}
            aria-label={`delete ${project.name}`}>delete</button>
        </div>
      </td>
    </tr>
  );
}

export default function ProjectsPage() {
  const { user } = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const projects = useProjects();
  const activeRun = useActiveRun();
  const [toDelete, setToDelete] = useState(null);
  const [actionError, setActionError] = useState("");
  const remove = useMutation({
    mutationFn: (project) => deleteProject(project.id),
    onSuccess: () => {
      setToDelete(null);
      queryClient.invalidateQueries({ queryKey: keys.projects });
    },
    onError: (error) => {
      setToDelete(null);
      setActionError(messageFor(error)); // PROJECT_HAS_ACTIVE_RUN, or a deleted-meanwhile NOT_FOUND
      queryClient.invalidateQueries({ queryKey: keys.projects });
    },
  });

  const runBlockedBy = !user?.is_email_verified ? VERIFY_REASON : activeRun.data ? ACTIVE_RUN_REASON : "";
  const items = projects.data?.items ?? [];

  return (
    <main className="page">
      <div className="page-header">
        <div>
          <h1>Projects</h1>
          <p className="page-subtitle">The code the agent works on. Every run uses a copy, never the original.</p>
        </div>
        <Link className="button button-primary" to="/projects/new">+ new project</Link>
      </div>

      {location.state?.notice && <Notice kind="info">{location.state.notice}</Notice>}
      {actionError && <Notice kind="error">{actionError}</Notice>}

      {projects.isPending && <div className="center-block"><Spinner label="Loading projects" /></div>}
      {projects.isError && (
        <Notice kind="error" action={<button type="button" className="notice-action" onClick={() => projects.refetch()}>retry</button>}>
          {messageFor(projects.error)}
        </Notice>
      )}
      {projects.isSuccess && items.length === 0 && (
        <EmptyState title="No projects yet - create one" action={<Link className="button" to="/projects/new">new project</Link>} />
      )}
      {projects.isSuccess && items.length > 0 && (
        <div className="table-card card">
          <table className="table projects-table">
            <thead>
              <tr>
                <th scope="col">name</th>
                <th scope="col">language</th>
                <th scope="col">source</th>
                <th scope="col">branch</th>
                <th scope="col">test command</th>
                <th scope="col" className="th-actions">actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((project) => (
                <ProjectRow key={project.id} project={project} runBlockedBy={runBlockedBy}
                  onDelete={(p) => { setActionError(""); setToDelete(p); }} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {toDelete && (
        <ConfirmDialog title="Delete project?" busy={remove.isPending}
          onCancel={() => setToDelete(null)} onConfirm={() => !remove.isPending && remove.mutate(toDelete)}>
          {toDelete.name} and all its runs will be deleted.
        </ConfirmDialog>
      )}
    </main>
  );
}
