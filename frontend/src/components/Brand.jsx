export default function Brand({ size = 20 }) {
  return (
    <span className="brand" style={{ fontSize: size }} aria-label="SANGRA_AI">
      <span className="brand-prompt" aria-hidden="true">&gt;</span>
      SANGRA_AI
      <span className="brand-cursor" aria-hidden="true">&#9612;</span>
    </span>
  );
}
