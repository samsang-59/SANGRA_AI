// One function per project route (F2.2).
import { request } from "./client.js";

const id = (value) => encodeURIComponent(String(value));

export const createProject = (project) => request("POST", "/projects", { body: project });
/** Returns { items, total } (newest first). */
export const listProjects = () => request("GET", "/projects");
export const getProject = (projectId) => request("GET", `/projects/${id(projectId)}`);
export const updateProject = (projectId, changes) => request("PATCH", `/projects/${id(projectId)}`, { body: changes });
export const deleteProject = (projectId) => request("DELETE", `/projects/${id(projectId)}`);
