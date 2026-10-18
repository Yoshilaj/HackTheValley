// A large card on the Daily tasks page: icon, title, one line of what it is,
// how long it takes (or "Done today"), and the task itself underneath.
export const TASK_ICONS = {
  checkin: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M8 12.5l2.5 2.5L16 9.5" />
    </>
  ),
  bp: (
    <>
      <path d="M12 21s-7-5.2-7-11a7 7 0 0114 0c0 5.8-7 11-7 11z" />
      <path d="M8.5 11h2l1.5-3 2 6 1.5-3h2" />
    </>
  ),
  tapping: (
    <>
      <rect x="3" y="7" width="18" height="12" rx="2" />
      <path d="M7 11h2M11 11h2M15 11h2M8 15h8" />
    </>
  ),
  vowel: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0014 0M12 18v3M9 21h6" />
    </>
  ),
  story: (
    <>
      <path d="M4 5a2 2 0 012-2h12v16H6a2 2 0 00-2 2z" />
      <path d="M4 19V5M8 7h6M8 10h4" />
    </>
  ),
};

export default function TaskCard({ id, title, sub, time, done, wide = false, children }) {
  return (
    <section className={`card task-card ${done ? "done" : ""} ${wide ? "span-2" : ""}`} aria-labelledby={`task-${id}-title`}>
      <header className="task-card-head">
        <span className="task-tile-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">{TASK_ICONS[id]}</svg>
        </span>
        <div className="task-card-text">
          <h2 id={`task-${id}-title`}>{title}</h2>
          <p>{sub}</p>
        </div>
        <span className={`task-status ${done ? "done" : ""}`}>
          {done ? (
            <>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
              Done today
            </>
          ) : (
            time
          )}
        </span>
      </header>
      <div className="task-card-body">{children}</div>
    </section>
  );
}
