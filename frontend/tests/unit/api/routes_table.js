// The table of every route function (FE1-F10): what it is called with, and the request it must make.
// Shared by the unit test (fake backend) and the contract test (the backend's own route list).
import * as auth from "../../../src/api/auth.js";
import * as projects from "../../../src/api/projects.js";
import * as runs from "../../../src/api/runs.js";

export const ROUTE_TABLE = [
  ["register", () => auth.register("Ann", "ann@example.com", "Str0ng-pass"), "POST", "/auth/register",
    { name: "Ann", email: "ann@example.com", password: "Str0ng-pass" }],
  ["login", () => auth.login("ann@example.com", "Str0ng-pass"), "POST", "/auth/login",
    { email: "ann@example.com", password: "Str0ng-pass" }],
  ["verifyEmail", () => auth.verifyEmail("tok"), "POST", "/auth/verify-email", { token: "tok" }],
  ["resendVerification", () => auth.resendVerification(), "POST", "/auth/resend-verification", undefined],
  ["forgotPassword", () => auth.forgotPassword("ann@example.com"), "POST", "/auth/forgot-password",
    { email: "ann@example.com" }],
  ["resetPassword", () => auth.resetPassword("tok", "N3w-pass-word"), "POST", "/auth/reset-password",
    { token: "tok", new_password: "N3w-pass-word" }],
  ["changePassword", () => auth.changePassword("Old-pass1", "N3w-pass-word"), "POST", "/auth/change-password",
    { old_password: "Old-pass1", new_password: "N3w-pass-word" }],
  ["getMe", () => auth.getMe(), "GET", "/auth/me", undefined],
  ["createProject", () => projects.createProject({ name: "Shop" }), "POST", "/projects", { name: "Shop" }],
  ["listProjects", () => projects.listProjects(), "GET", "/projects", undefined],
  ["getProject", () => projects.getProject(3), "GET", "/projects/3", undefined],
  ["updateProject", () => projects.updateProject(3, { name: "New" }), "PATCH", "/projects/3", { name: "New" }],
  ["deleteProject", () => projects.deleteProject(3), "DELETE", "/projects/3", undefined],
  ["startRun", () => runs.startRun(3, "The summer sale shows 5% discount."), "POST", "/projects/3/runs",
    { bug_description: "The summer sale shows 5% discount." }],
  ["listRuns", () => runs.listRuns({ projectId: 3, status: "active", page: 2, limit: 20 }), "GET",
    "/runs?project_id=3&status=active&page=2&limit=20", undefined],
  ["getRun", () => runs.getRun(5), "GET", "/runs/5", undefined],
  ["submitApproval", () => runs.submitApproval(5, 11, "rejected", "Also change the config file."), "POST",
    "/runs/5/approval", { attempt_id: 11, decision: "rejected", feedback: "Also change the config file." }],
  ["sendHint", () => runs.sendHint(5, "Look at Discount.java"), "POST", "/runs/5/hint",
    { text: "Look at Discount.java" }],
  ["cancelRun", () => runs.cancelRun(5), "POST", "/runs/5/cancel", undefined],
  ["applyToSource", () => runs.applyToSource(5), "POST", "/runs/5/apply-to-source", undefined],
  ["downloadPackage", () => runs.downloadPackage(5, { save: () => {} }), "GET", "/runs/5/package", undefined],
  ["deleteRun", () => runs.deleteRun(5), "DELETE", "/runs/5", undefined],
];
