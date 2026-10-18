import { useState } from "react";
import RiskAssessment from "../components/RiskAssessment.jsx";
import QuickChat from "../components/QuickChat.jsx";
import ConditionRisks from "../components/ConditionRisks.jsx";
import HeartSnapshotChart from "../components/HeartSnapshotChart.jsx";
import DetailSheet from "../components/DetailSheet.jsx";
import GuestGate from "../components/GuestGate.jsx";
import { useCareAlert } from "../context/CareAlertContext.jsx";
import { useSession } from "../context/SessionContext.jsx";
import { RISK_ABOUT, RISK_TIPS } from "../content/copy.js";
import { useProfile } from "../context/ProfileContext.jsx";
import { basisText, changeSince } from "../utils/risk.js";
import { ageFrom, capitalize, relativeDay } from "../utils/dates.js";

export default function Overview() {
  const [detailOpen, setDetailOpen] = useState(false);
  const { score, level, previous, updatedAt, basis, conditions } = useCareAlert().risk;
  const { isGuest } = useSession();
  const { profile: patient } = useProfile();
  const change = changeSince(score, previous?.score);

  // What "how is this worked out" shows for the overall index.
  const riskItem = {
    label: "Overall risk",
    value: String(score),
    unit: "/100",
    badge: `${level} risk`,
    trend: level === "Low" ? "normal" : "watch",
    details: {
      about: RISK_ABOUT,
      rows: [
        change && ["Change", change],
        conditions?.length > 0 && ["Combines", conditions.map((c) => c.name).join(", ")],
        ["Based on", capitalize(basisText(basis))],
        ["Checked against", "Clinical guidelines (NICE, WHO)"],
        updatedAt && ["Updated", capitalize(relativeDay(updatedAt))],
      ].filter(Boolean),
      tips: RISK_TIPS,
    },
  };

  return (
    <main className="page">
      <div>
        <h1 tabIndex={-1}>{isGuest ? "Welcome to CareSync" : `Good morning, ${patient.name}`}</h1>
        <p className="subtitle">
          {isGuest ? "You're browsing as a guest." : "Here's how your health looks today."}
        </p>
      </div>

      <div className="stack">
        {isGuest ? (
          <GuestGate title="See your health at a glance">
            Sign in for your personal risk assessment, worked out from your readings, check-ins and daily tasks.
          </GuestGate>
        ) : (
          <div className="risk-row">
            <div className="risk-main">
              <RiskAssessment
                score={score}
                level={level}
                previous={previous}
                updatedAt={updatedAt}
                basis={basis}
                conditionCount={conditions?.length ?? 0}
                onOpen={() => setDetailOpen(true)}
              />
              <HeartSnapshotChart showLink height={140} />
            </div>
            <div className="risk-side">
              <ConditionRisks items={conditions} age={ageFrom(patient.dateOfBirth)} updatedAt={updatedAt} basis={basis} />
            </div>
          </div>
        )}
        <QuickChat />
      </div>

      <DetailSheet item={detailOpen ? riskItem : null} onClose={() => setDetailOpen(false)} />
    </main>
  );
}
