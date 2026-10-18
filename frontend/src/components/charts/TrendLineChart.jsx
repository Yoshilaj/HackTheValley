import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import { ChartBox } from "./ChartFrame.jsx";
import { AXIS, GRID, MUTED, PRIMARY, activeDotFor, dotFor, tickStyle, tooltipStyle } from "./theme.js";
import { shortDate } from "../../utils/dates.js";

// A line chart of one or more series over time.
//   data:    [{ [xKey]: "YYYY-MM-DD" | label, [series key]: number | null, ... }], oldest first
//   series:  [{ key, name, color, dashed?, opacity?, dot? }]  (legend shows when there are 2+)
//   band:    [low, high] shaded as the healthy range; refLines: [{ y, label }]
//   yDomain: [min, max] or ["auto", "auto"]; unit: shown after values in the tooltip
export default function TrendLineChart({
  data,
  series,
  xKey = "date",
  xFormat = shortDate,
  yDomain = ["auto", "auto"],
  yTicks,
  unit = "",
  band,
  refLines = [],
  height = 180,
  label,
  valueFormat = (v) => (Number.isFinite(v) ? Math.round(v * 10) / 10 : v),
}) {
  // Dots only on short series; past a dozen points they bead the line.
  const showDots = data.length <= 12;
  return (
    <ChartBox height={height} label={label}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid vertical={false} stroke={GRID} strokeWidth={1} />
        <XAxis
          dataKey={xKey}
          tick={tickStyle}
          tickLine={false}
          axisLine={{ stroke: AXIS }}
          tickFormatter={xFormat}
          minTickGap={28}
          interval="preserveStartEnd"
        />
        <YAxis tick={tickStyle} tickLine={false} axisLine={false} domain={yDomain} ticks={yTicks} width={44} />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={xFormat}
          formatter={(v, name) => [`${valueFormat(v)}${unit ? ` ${unit}` : ""}`, name]}
        />
        {series.length > 1 && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: MUTED }} />}
        {band && <ReferenceArea y1={band[0]} y2={band[1]} fill={PRIMARY} fillOpacity={0.08} stroke="none" />}
        {refLines.map((r) => (
          <ReferenceLine
            key={`${r.y}-${r.label}`}
            y={r.y}
            stroke={r.color ?? MUTED}
            strokeDasharray="4 4"
            label={r.label ? { value: r.label, position: "insideTopRight", fontSize: 11, fill: MUTED } : undefined}
          />
        ))}
        {series.map((s) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            stroke={s.color}
            strokeWidth={2}
            strokeDasharray={s.dashed ? "6 4" : undefined}
            strokeOpacity={s.opacity ?? 1}
            dot={showDots ? (s.dot ?? dotFor(s.color)) : false}
            activeDot={activeDotFor(s.color)}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartBox>
  );
}
