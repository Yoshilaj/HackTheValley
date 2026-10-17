import { coverage } from "../data/placeholder.js";

export default function CoverageStack() {
  const [a, b, c] = coverage.stack;
  const { front } = coverage;

  return (
    <>
      <div className="card-head">
        <h2>
          My coverage <sup>{coverage.count}</sup>
        </h2>
        <button className="add-btn">
          Add <i aria-hidden="true">+</i>
        </button>
      </div>

      <div className="coverage-stack">
        <div className="plan plan-1">
          <span>ID {a.last4}</span>
        </div>
        <div className="plan plan-2">
          <b>{b.brand}</b>
          <span>ID {b.last4}</span>
        </div>
        <div className="plan plan-3">
          <b>{c.brand}</b>
          <span>ID {c.last4}</span>
        </div>
        <div className="plan plan-4">
          <p className="num">{front.number}</p>
          <p className="holder">
            <span>{front.holder}</span>
            <span>Renews {front.expiry}</span>
          </p>
        </div>
      </div>
    </>
  );
}
