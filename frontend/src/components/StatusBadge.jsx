import { statusTone } from "../runStatus.js";

export default function StatusBadge({ status, large = false }) {
  return (
    <span className={`badge badge-${statusTone(status)}${large ? " badge-large" : ""}`}>
      <span className="badge-dot" aria-hidden="true" />
      {status}
    </span>
  );
}
