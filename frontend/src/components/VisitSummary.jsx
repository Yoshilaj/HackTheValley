import { createPortal } from "react-dom";
import { useProfile } from "../context/ProfileContext.jsx";
import useVitals from "../hooks/useVitals.js";
import { ageFrom } from "../utils/dates.js";

// One-page summary to print and bring to an appointment: who you are, the
// visit, your latest readings and space for questions.
// Rendered into <body> only while printing; print CSS hides the app around it.
export default function VisitSummary({ appointment: a }) {
  const { vitals } = useVitals();
  const { profile: patient } = useProfile();
  const born =
    ageFrom(patient.dateOfBirth) == null
      ? ""
      : new Date(`${patient.dateOfBirth}T00:00`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  const today = new Date().toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });

  return createPortal(
    <article className="print-area" aria-hidden="true">
      <header className="print-head">
        <h1>Visit summary</h1>
        <p>Prepared with CareSync on {today}</p>
      </header>

      <section>
        <h2>Patient</h2>
        <dl>
          <dt>Name</dt>
          <dd>{patient.fullName}</dd>
          <dt>Date of birth</dt>
          <dd>
            {born ? `${born} (age ${ageFrom(patient.dateOfBirth)})` : "Not recorded"}
          </dd>
          <dt>Health card</dt>
          <dd>Ending {patient.healthCardLast4}</dd>
          <dt>Allergies</dt>
          <dd>{patient.allergies.join(", ") || "None recorded"}</dd>
          <dt>Conditions</dt>
          <dd>{patient.conditions.join(", ") || "None recorded"}</dd>
        </dl>
      </section>

      <section>
        <h2>Appointment</h2>
        <dl>
          <dt>With</dt>
          <dd>{[a.name, a.specialty].filter(Boolean).join(", ")}</dd>
          <dt>Where</dt>
          <dd>{[a.place, a.address].filter(Boolean).join(", ")}</dd>
          <dt>When</dt>
          <dd>{[a.date, a.time, a.length].filter(Boolean).join(" · ")}</dd>
          {a.note && (
            <>
              <dt>Reason</dt>
              <dd>{a.note}</dd>
            </>
          )}
        </dl>
      </section>

      <section>
        <h2>Latest readings</h2>
        <table>
          <thead>
            <tr>
              <th>Reading</th>
              <th>Latest</th>
              <th>Healthy range</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {vitals.map((v) => (
              <tr key={v.id}>
                <td>{v.label}</td>
                <td>
                  {v.value} {v.unit}
                </td>
                <td>{v.range}</td>
                <td>{v.status === "watch" ? "Watch" : "Normal"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Questions for my doctor</h2>
        <div className="print-lines">
          {Array.from({ length: 5 }, (_, i) => (
            <span key={i} />
          ))}
        </div>
      </section>
    </article>,
    document.body
  );
}
