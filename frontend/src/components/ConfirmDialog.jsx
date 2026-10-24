// A confirm box (foundations: ConfirmDialog). Keyboard: the focus starts on "cancel", Tab stays inside the box,
// Escape cancels, and the focus goes back to the button that opened it (FE3-U02).
import { useEffect, useId, useRef } from "react";

export default function ConfirmDialog({ title, children, confirmLabel = "delete", cancelLabel = "cancel",
  busyText = "deleting\u2026", busy = false, onConfirm, onCancel, danger = true }) {
  const id = useId();
  const box = useRef(null);
  const cancelRef = useRef(null);

  useEffect(() => {
    const opener = document.activeElement;
    cancelRef.current?.focus();
    return () => {
      if (opener && typeof opener.focus === "function" && document.contains(opener)) opener.focus();
    };
  }, []);

  function onKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!busy) onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...box.current.querySelectorAll("button:not([disabled])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}>
      <div ref={box} className="dialog" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-text`} onKeyDown={onKeyDown}>
        <h2 id={`${id}-title`}>{title}</h2>
        <p id={`${id}-text`} className="dialog-text">{children}</p>
        <div className="row-end">
          <button ref={cancelRef} type="button" className="button" onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          <button type="button" className={`button ${danger ? "button-danger-solid" : "button-primary"}`}
            onClick={onConfirm} disabled={busy} aria-busy={busy || undefined}>
            {busy ? busyText : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
