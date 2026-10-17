import { Link } from "react-router-dom";
import { medications } from "../data/placeholder.js";

export default function Medications() {
  return (
    <div className="meds">
      <div className="card-head">
        <h2>
          Medications <sup>{medications.count}</sup>
        </h2>
        <Link to="/medications" className="see-all">
          Manage ›
        </Link>
      </div>

      <div className="med-icons" aria-hidden="true">
        {medications.icons.map((i) => (
          <i key={i.id} style={{ background: i.bg, color: i.ink }}>
            {i.glyph}
          </i>
        ))}
      </div>

      <ul className="med-list">
        {medications.list.map((s) => (
          <li key={s.id}>
            <i aria-hidden="true">{s.glyph}</i>
            <div>
              <b>{s.name}</b>
              <small>{s.next}</small>
            </div>
            <strong>
              {s.dose}
              <small>{s.unit}</small>
            </strong>
            <button className="dots" aria-label={`More options for ${s.name}`}>⋮</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
