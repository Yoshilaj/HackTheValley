import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from "recharts";
import { ChartBox } from "./ChartFrame.jsx";
import { AXIS, GRID, MUTED, tickStyle, tooltipStyle } from "./theme.js";
import { shortDate } from "../../utils/dates.js";

// Columns per day (steps, hours of sleep). series: [{ key, name, color }].
export default function BarsChart({ data, series, xKey = "date", xFormat = shortDate, unit = "", height = 160, label, valueFormat }) {
  const fmt = valueFormat ?? ((v) => (Number.isFinite(v) ? Math.round(v * 10) / 10 : v));
  return (
    <ChartBox height={height} label={label}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }} barGap={2} barCategoryGap="30%">
        <CartesianGrid vertical={false} stroke={GRID} strokeWidth={1} />
        <XAxis dataKey={xKey} tick={tickStyle} tickLine={false} axisLine={{ stroke: AXIS }} tickFormatter={xFormat} minTickGap={28} interval="preserveStartEnd" />
        <YAxis tick={tickStyle} tickLine={false} axisLine={false} width={44} tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 100) / 10}K` : v)} />
        <Tooltip {...tooltipStyle} cursor={{ fill: "rgba(0,0,0,0.04)" }} labelFormatter={xFormat} formatter={(v, name) => [`${fmt(v)}${unit ? ` ${unit}` : ""}`, name]} />
        {series.length > 1 && <Legend iconType="circle" wrapperStyle={{ fontSize: 12, color: MUTED }} />}
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
        ))}
      </BarChart>
    </ChartBox>
  );
}
