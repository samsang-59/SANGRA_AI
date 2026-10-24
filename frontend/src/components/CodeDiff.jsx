// One file edit as old code and new code (foundations: CodeDiff): the file, the lines, the edit number, then the
// old lines (red, "-") and the new lines (green, "+"), and the reason. Code is always text, never run (FE5-S02).
function lines(code) {
  if (code === "") return [];
  const text = code.endsWith("\n") ? code.slice(0, -1) : code;
  return text.split("\n");
}

export default function CodeDiff({ edit, showReason = true }) {
  const oldLines = lines(edit.old_code ?? "");
  const newLines = lines(edit.new_code ?? "");
  return (
    <figure className="diff card">
      <figcaption className="diff-header">
        <span className="diff-file">{edit.file_path}</span>
        <span className="diff-meta">lines {edit.start_line}{"–"}{edit.end_line} {"·"} edit {edit.edit_order}</span>
      </figcaption>
      <div className="diff-body" role="table" aria-label={`changes in ${edit.file_path}`} tabIndex={0}>
        {oldLines.map((line, i) => (
          <div className="diff-line diff-old" role="row" key={`o${i}`}>
            <span className="diff-number" role="cell">{edit.start_line + i}</span>
            <span className="diff-mark" role="cell">-</span>
            <span className="diff-code" role="cell">{line}</span>
          </div>
        ))}
        {newLines.length === 0 ? (
          <div className="diff-line diff-new" role="row">
            <span className="diff-number" role="cell">{edit.start_line}</span>
            <span className="diff-mark" role="cell">+</span>
            <span className="diff-code diff-deleted" role="cell">(line deleted)</span>
          </div>
        ) : newLines.map((line, i) => (
          <div className="diff-line diff-new" role="row" key={`n${i}`}>
            <span className="diff-number" role="cell">{edit.start_line + i}</span>
            <span className="diff-mark" role="cell">+</span>
            <span className="diff-code" role="cell">{line}</span>
          </div>
        ))}
      </div>
      {showReason && edit.explanation && <p className="diff-reason">Reason: {edit.explanation}</p>}
    </figure>
  );
}
