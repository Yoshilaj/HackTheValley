// Chart colours as hex literals: Recharts writes SVG presentation attributes,
// where CSS variables don't resolve reliably. Mirrors the tokens in global.css.
//
// Categorical slots, in fixed order (validated with the dataviz palette checker
// on the white card surface: every adjacent pair clears the colour-vision and
// normal-vision floors). Slot 3 (aqua) sits under 3:1 contrast on white, so
// every chart with it also carries a legend and tooltips (the relief rule).
export const SERIES = ["#155bd0", "#eb6834", "#1baf7a", "#6250d6"];

// One colour per risk pathway, by entity (never by rank), so a pathway keeps its
// colour whichever subset of pathways a chart shows.
export const PATHWAYS = [
  { id: "cardio", name: "Heart & blood pressure", color: SERIES[0] },
  { id: "cognitive", name: "Memory", color: SERIES[1] },
  { id: "parkinsons", name: "Movement & voice", color: SERIES[2] },
  { id: "general", name: "General wellbeing", color: SERIES[3] },
];

export const WARN = "#c26a00"; // the "watch" amber used on the vitals tiles
export const PRIMARY = SERIES[0];
export const SECONDARY = SERIES[1];

// Chrome: recessive hairlines and muted ink.
export const GRID = "#e5e5ea";
export const AXIS = "#c7c7cc";
export const MUTED = "#6e6e73";
export const TEXT = "#1d1d1f";
export const SURFACE = "#ffffff";

export const tickStyle = { fontSize: 11, fill: MUTED, fontFamily: "inherit" };

export const tooltipStyle = {
  contentStyle: {
    borderRadius: 10,
    border: "1px solid rgba(0,0,0,0.08)",
    boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
    fontSize: 13,
    padding: "8px 12px",
    background: SURFACE,
  },
  labelStyle: { color: MUTED, marginBottom: 4 },
  itemStyle: { color: TEXT, padding: 0 },
  cursor: { stroke: AXIS, strokeWidth: 1 },
};

// A dot with a 2px ring in the surface colour, so it stays legible where lines cross.
export const dotFor = (color) => ({ r: 4, fill: color, stroke: SURFACE, strokeWidth: 2 });
export const activeDotFor = (color) => ({ r: 5, fill: color, stroke: SURFACE, strokeWidth: 2 });
