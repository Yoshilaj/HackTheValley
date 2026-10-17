import { useEffect, useRef } from "react";

// Glass popup for a dashboard card. Uses native <dialog> so focus trapping,
// Esc-to-close and the backdrop come from the browser.
export default function DetailSheet({ item, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (item && !dialog.open) dialog.showModal();
    if (!item && dialog.open) dialog.close();
  }, [item]);

  // Clicks on the backdrop land on the <dialog> element itself.
  const onClick = (e) => {
    if (e.target === ref.current) onClose();
  };

  return (
    <dialog ref={ref} className="sheet" aria-labelledby="sheet-title" onClose={onClose} onClick={onClick}>
      {item && (
        <div className="sheet-body">
          <header className="sheet-head">
            <div>
              <p className="label">{item.label}</p>
              <p id="sheet-title" className="sheet-value">
                {item.value}
                {item.unit && <sup>{item.unit}</sup>}
              </p>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          {item.badge && <em className={`badge ${item.trend}`}>{item.badge}</em>}
          <p className="sheet-about">{item.details.about}</p>

          <dl className="sheet-rows">
            {item.details.rows.map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>

          <h3 className="sheet-sub">Tips</h3>
          <ul className="sheet-tips">
            {item.details.tips.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      )}
    </dialog>
  );
}
