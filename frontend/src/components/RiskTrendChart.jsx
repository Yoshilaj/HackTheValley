import ChartFrame from "./charts/ChartFrame.jsx";
import TrendLineChart from "./charts/TrendLineChart.jsx";
import { PATHWAYS } from "./charts/theme.js";
import { AUTO_BOOK_THRESHOLD } from "../context/CareAlertContext.jsx";
import { weekLabel } from "../utils/dates.js";

// Follow-up priority (0-100) per pathway, one point per week over the last 8
// weeks, from the `trend` on each risk card. A week without an assessment is a
// gap. The dashed line is where the auto-booking pop-up opens.
export default function RiskTrendChart({ cards }) {
  const byWeek = new Map();
  for (const card of cards ?? []) {
    for (const p of card.trend ?? []) {
      if (!byWeek.has(p.week)) byWeek.set(p.week, { week: p.week });
      byWeek.get(p.week)[card.pathway] = p.priority_score ?? null;
    }
  }
  const data = [...byWeek.values()].sort((a, b) => a.week.localeCompare(b.week));
  const present = PATHWAYS.filter((p) => data.some((d) => Number.isFinite(d[p.id])));
  const series = present.map((p) => ({ key: p.id, name: p.name, color: p.color }));

  return (
    <ChartFrame
      id="risk-trend"
      title="Follow-up priority by week"
      tag="8 weeks"
      empty={!series.length ? "Your weekly trend appears after the first scheduled assessment." : null}
      note="0-100 per area, from your readings, check-ins and checks. Not a probability of disease."
    >
      <TrendLineChart
        data={data}
        series={series}
        xKey="week"
        xFormat={weekLabel}
        yDomain={[0, 100]}
        yTicks={[0, 25, 50, 75, 100]}
        refLines={[{ y: AUTO_BOOK_THRESHOLD, label: "Auto-booking" }]}
        label={`Follow-up priority by week for ${series.map((s) => s.name).join(", ")}`}
      />
    </ChartFrame>
  );
}
