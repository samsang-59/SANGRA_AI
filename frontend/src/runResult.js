// Rules of the result view (05_frontend_design.md F6), shared by its components and the tests.
export const NO_TEST_COVERS = "Tests passed, but no test covers this bug, please verify the fix yourself";
export const PACKAGE_EXPIRED_TEXT = "The package expired (kept 7 days)";

/** "Apply to my folder" is offered only for a succeeded run of a local-path project that was not applied yet. */
export const canApply = (run) => run.status === "succeeded" && run.source_type === "local_path" &&
  !run.applied_to_source_at;

/** The baseline and the newest after-fix result (the one of the last tested attempt). */
export function pickResults(run) {
  const results = run.test_results ?? [];
  const baseline = results.find((r) => r.test_type === "baseline") ?? null;
  const afterFix = results.filter((r) => r.test_type === "after_fix").at(-1) ?? null;
  const attempt = afterFix && (run.attempts ?? []).find((a) => a.id === afterFix.attempt_id);
  return { baseline, afterFix, attemptNumber: attempt?.attempt_number ?? null };
}

/** The summary without the "no test covers" sentence, which is shown as its own warning; warn when it applies (the
 * run succeeded but the baseline had already passed). */
export function splitSummary(run) {
  const summary = run.final_summary ?? "";
  const { baseline } = pickResults(run);
  const labelled = summary.includes(NO_TEST_COVERS);
  const warn = run.status === "succeeded" && (labelled || baseline?.status === "passed");
  const text = labelled ? summary.replace(`${NO_TEST_COVERS}.`, "").replace(NO_TEST_COVERS, "").trim() : summary;
  return { text, warn };
}
