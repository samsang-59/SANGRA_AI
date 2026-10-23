// Stand-in for a page that a later phase builds (FE2 to FE6). Each one is replaced by the real page in its phase.
export default function PlannedPage({ title, phase }) {
  return (
    <main style={{ padding: "var(--page-padding)" }}>
      <h1>{title}</h1>
      <p style={{ color: "var(--muted)" }}>This page is built in phase {phase}.</p>
    </main>
  );
}
