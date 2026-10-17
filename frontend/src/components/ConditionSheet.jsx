import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useChatHistory } from "../context/ChatHistoryContext.jsx";
import RiskBar from "./RiskBar.jsx";
import { basisText, changeSince, conditionLevel, formatPct, naturalFrequency } from "../utils/risk.js";
import { capitalize, relativeDay } from "../utils/dates.js";

// Liquid glass sheet explaining one condition estimate: the % chance in plain
// words and as a 100-person picture, how it compares with a typical healthy
// person the same age, the method, and the evidence behind it - the user's
// chats, past doctors' diagnoses, and medical sources the backend retrieved
// (RAG). Native <dialog> like DetailSheet.

// 100 dots, `count` of them filled: the standard way to show "x in 100".
function IconArray({ count }) {
  return (
    <div className="icon-array" role="img" aria-label={`${count} out of 100 people`}>
      {Array.from({ length: 100 }, (_, i) => (
        <span key={i} className={i < count ? "on" : undefined} />
      ))}
    </div>
  );
}

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

export default function ConditionSheet({ condition, age, updatedAt, basis, onClose }) {
  const ref = useRef(null);
  const { chats } = useChatHistory();

  useEffect(() => {
    const dialog = ref.current;
    if (condition && !dialog.open) dialog.showModal();
    if (!condition && dialog.open) dialog.close();
  }, [condition]);

  const c = condition;
  const d = c?.details ?? {};
  const level = c && conditionLevel(c);
  const freq = c && naturalFrequency(c.risk);
  const change = c && changeSince(c.risk, c.previous, formatPct);
  const peer = age ? `healthy ${age}-year-old` : "healthy person your age";

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
              <p className="label">{c.horizon}-year risk</p>
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
              <p className="condition-big">
                {formatPct(c.risk)} <small>chance in the next {c.horizon} years</small>
              </p>
              <span className="condition-chip">{level} risk</span>
            </div>
            <RiskBar
              c={c}
              label={`Your risk ${formatPct(c.risk)}${c.typical != null ? `, typical for a ${peer} ${formatPct(c.typical)}` : ""}`}
            />
            <p className="risk-legend" aria-hidden="true">
              <span>
                <i className="legend-you" /> You {formatPct(c.risk)}
              </span>
              {c.typical != null && (
                <span>
                  <i className="legend-typical" /> Typical {peer} {formatPct(c.typical)}
                </span>
              )}
            </p>
            <p className="condition-plain">
              About {freq.count} in {freq.of.toLocaleString("en-US")} people with a profile like yours will {c.outcome ?? "develop this"} in
              the next {c.horizon} years.
            </p>
            {freq.of === 100 && <IconArray count={freq.count} />}
            {d.summary && <p>{d.summary}</p>}
          </div>

          <dl className="sheet-rows">
            {c.method && (
              <div>
                <dt>Method</dt>
                <dd>{c.method}</dd>
              </div>
            )}
            {change && (
              <div>
                <dt>Change</dt>
                <dd>{change}</dd>
              </div>
            )}
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
