// The agent log (foundations: EventTimeline): every event in order, with its number and a sign for its type.
// It follows new events while the reader is at the bottom; scrolled up, it stays put and offers "jump to latest"
// (FE5-U02). Long messages are cut with "show more" (FE5-X08). Everything is plain text (FE5-S01).
import { useEffect, useLayoutEffect, useRef, useState } from "react";

const SIGNS = {
  status_changed: ["●", "accent"],
  tool_called: ["›", "info"],
  tool_result: ["←", "muted"],
  hint_requested: ["?", "accent"],
  hint_received: ["←", "muted"],
  approval_requested: ["?", "accent"],
  tests_started: ["▸", "success"],
  tests_finished: ["▸", "success"],
  error: ["!", "danger"],
};
const STRONG = ["hint_requested", "approval_requested", "approval_decided"];
export const MESSAGE_CUT = 400;

function sign(event) {
  if (event.event_type === "approval_decided") {
    return event.details?.decision === "rejected" ? ["✕", "danger"] : ["✓", "success"];
  }
  return SIGNS[event.event_type] ?? ["·", "muted"]; // an unknown type is shown plainly (FE5-X07)
}

function text(event) {
  if (event.event_type === "status_changed" && event.details?.status) return `status → ${event.details.status}`;
  return event.message ?? "";
}

function Line({ event }) {
  const [open, setOpen] = useState(false);
  const [mark, tone] = sign(event);
  const full = text(event);
  const long = full.length > MESSAGE_CUT;
  const shown = long && !open ? `${full.slice(0, MESSAGE_CUT)}…` : full;
  const kind = event.event_type === "error" ? " log-error" : STRONG.includes(event.event_type) ? " log-strong" : "";
  return (
    <li className={`log-line${kind}`}>
      <span className="log-number">{String(event.sequence_number).padStart(3, "0")}</span>
      <span className={`log-sign text-${tone}`} aria-hidden="true">{mark}</span>
      <span className="log-text">
        {shown}
        {long && (
          <button type="button" className="link-button log-more" onClick={() => setOpen((v) => !v)}>
            {open ? "show less" : "show more"}
          </button>
        )}
      </span>
    </li>
  );
}

export default function EventTimeline({ events, header, footer }) {
  const scroller = useRef(null);
  const atBottom = useRef(true);
  const [behind, setBehind] = useState(false);

  function onScroll() {
    const box = scroller.current;
    atBottom.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
    if (atBottom.current) setBehind(false);
  }

  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box) return;
    if (atBottom.current) box.scrollTop = box.scrollHeight;
  }, [events.length]);

  useEffect(() => {
    // New events while the reader looks further up: offer the jump instead of moving the view.
    if (!atBottom.current && events.length) setBehind(true);
  }, [events.length]);

  function jump() {
    const box = scroller.current;
    box.scrollTop = box.scrollHeight;
    atBottom.current = true;
    setBehind(false);
  }

  return (
    <section className="card log-card" aria-label="agent log">
      <div className="log-header">{header}</div>
      <div ref={scroller} className="log-scroll" onScroll={onScroll} tabIndex={0} aria-label="agent log events">
        {events.length === 0 ? <p className="log-empty">Waiting for the first event{"…"}</p> : (
          <ol className="log-list">
            {events.map((event) => <Line key={event.sequence_number} event={event} />)}
          </ol>
        )}
      </div>
      {behind && (
        <button type="button" className="button button-small log-jump" onClick={jump}>{"↓"} jump to latest</button>
      )}
      {footer && <div className="log-footer">{footer}</div>}
    </section>
  );
}
