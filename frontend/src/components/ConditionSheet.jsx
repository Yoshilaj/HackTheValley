import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import { basisText } from "../utils/risk.js";
import { capitalize, relativeDay } from "../utils/dates.js";

// Liquid glass sheet explaining one condition: the finding, the method, and
// the evidence behind it - what the data shows, the user's chats, past doctors'
// diagnoses, and medical sources the backend retrieved (RAG). Native <dialog>
// like DetailSheet.
const LEVEL_CHIP = {
  insufficient_data: "Not enough data",
  invalid_measurement: "Check readings",
  no_finding: "No findings",
  watch: "Keep an eye on",
  followup_recommended: "Follow-up recommended",
};

function Section({ title, icon, empty, children, count }) {
  return (
    <section className="evidence">
      <h3>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          {icon}
        </svg>
        {title}
      </h3>
      {count ? <ul className="evidence-list">{children}</ul> : <p className="evidence-empty">{empty}</p>}
    </section>
  );
}

export default function ConditionSheet({ condition, updatedAt, basis, onClose }) {
  const ref = useRef(null);
  const { chats } = useChatHistory();

  useEffect(() => {
    const dialog = ref.current;
    if (condition && !dialog.open) dialog.showModal();
    if (!condition && dialog.open) dialog.close();
  }, [condition]);

  const c = condition;
  const d = c?.details ?? {};
  const level = c?.tone ?? "Low"; // colour follows the finding level
  // Backend pathways without a % show their finding and evidence only.
  const chip = c && (LEVEL_CHIP[c.level] ?? c.finding);

  return (
    <dialog
      ref={ref}
      className="sheet condition-sheet"
      aria-labelledby="condition-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      {c && (
        <div className={`sheet-body risk-tone ${level.toLowerCase()}`}>
          <header className="sheet-head">
            <div>
              <p className="label">Follow-up check</p>
              <h2 id="condition-title" className="book-title">
                {c.name}
              </h2>
            </div>
            <button className="sheet-close" aria-label="Close" onClick={onClose}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </header>

          <div className="condition-summary">
            <div className="condition-head">
              <p className="condition-big">{c.finding}</p>
              <span className="condition-chip">{chip}</span>
            </div>
            {d.summary && <p>{d.summary}</p>}
          </div>

          <dl className="sheet-rows">
            <div>
              <dt>Based on</dt>
              <dd>{capitalize(basisText(basis))}</dd>
            </div>
            {updatedAt && (
              <div>
                <dt>Updated</dt>
                <dd>{capitalize(relativeDay(updatedAt))}</dd>
              </div>
            )}
          </dl>

          <h3 className="sheet-sub">Why CareSync thinks this</h3>

          {d.findings && (
            <Section
              title="What your data shows"
              count={d.findings.length}
              empty="Nothing in your recent readings points to this."
              icon={<path d="M3 12h4l2-5 4 10 2-5h6" />}
            >
              {d.findings.map((f) => (
                <li key={f}>
                  <p>{f}</p>
                </li>
              ))}
            </Section>
          )}

          <Section
            title="From your chats"
            count={d.chats?.length}
            empty="Nothing in your chats points to this."
            icon={<path d="M20 12a8 8 0 01-11.6 7.1L4 20l1-4.1A8 8 0 1120 12z" />}
          >
            {d.chats?.map((e, i) => {
              const linked = e.chatId && chats.some((ch) => ch.id === e.chatId);
              return (
                <li key={i}>
                  <q>{e.quote}</q>
                  {e.why && <p>{e.why}</p>}
                  <small>
                    {linked ? <Link to={`/chat/${e.chatId}`} onClick={onClose}>Open chat</Link> : "Chat"} · {e.date}
                  </small>
                </li>
              );
            })}
          </Section>

          <Section
            title="From your doctors"
            count={d.diagnoses?.length}
            empty="No diagnoses on file about this."
            icon={
              <>
                <path d="M6 3v6a4 4 0 008 0V3" />
                <path d="M10 13v2a5 5 0 0010 0v-2" />
                <circle cx="20" cy="11" r="2" />
              </>
            }
          >
            {d.diagnoses?.map((e, i) => (
              <li key={i}>
                <p>{e.finding}</p>
                <small>
                  {[e.doctor, e.specialty].filter(Boolean).join(", ")} · {e.date}
                </small>
              </li>
            ))}
          </Section>

          <Section
            title="Medical sources CareSync checked"
            count={d.sources?.length}
            empty="No sources retrieved."
            icon={
              <>
                <path d="M4 5a2 2 0 012-2h12v16H6a2 2 0 00-2 2z" />
                <path d="M4 19V5M8 7h6" />
              </>
            }
          >
            {d.sources?.map((s, i) => (
              <li key={i}>
                <p className="evidence-source">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.title}
                    </a>
                  ) : (
                    s.title
                  )}
                </p>
                <q>{s.excerpt}</q>
                <small>{s.name}</small>
              </li>
            ))}
          </Section>

          {d.tips?.length > 0 && (
            <>
              <h3 className="sheet-sub">What helps</h3>
              <ul className="sheet-tips">
                {d.tips.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </>
          )}

          <p className="autobook-note">
            This is an estimate, not a diagnosis. Talk to your doctor about any concerns.
          </p>
        </div>
      )}
    </dialog>
  );
}
