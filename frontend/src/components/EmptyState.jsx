// The empty state of a list (foundations: EmptyState), e.g. "No projects yet - create one".
export default function EmptyState({ title, action = null, symbol = "{ }" }) {
  return (
    <div className="empty-state">
      <span className="empty-symbol" aria-hidden="true">{symbol}</span>
      <p className="empty-title">{title}</p>
      {action}
    </div>
  );
}
