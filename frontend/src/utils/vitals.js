// How each vital is entered on "Log a reading" and judged against the healthy
// range shown on its tile ("normal" or "watch"). The min/max limits catch
// typos; they are not medical thresholds.
export const VITAL_INPUTS = {
  bp: {
    label: "Blood pressure",
    unit: "mmHg",
    fields: [
      { key: "sys", label: "Systolic (top number)", min: 60, max: 260 },
      { key: "dia", label: "Diastolic (bottom number)", min: 30, max: 160 },
    ],
    check: ({ sys, dia }) => (sys < 120 && dia < 80 ? "normal" : "watch"),
    text: ({ sys, dia }) => `${sys}/${dia}`,
    trend: ({ sys }) => sys, // the tile's sparkline follows the top number
  },
  hr: {
    label: "Heart rate",
    unit: "bpm",
    fields: [{ key: "value", label: "Resting heart rate", min: 25, max: 250 }],
    check: ({ value }) => (value >= 60 && value <= 100 ? "normal" : "watch"),
  },
  glucose: {
    label: "Blood glucose",
    unit: "mmol/L",
    fields: [{ key: "value", label: "Blood glucose", min: 1, max: 35, decimals: 1 }],
    check: ({ value }) => (value >= 4 && value <= 5.6 ? "normal" : "watch"),
  },
  weight: {
    label: "Weight",
    unit: "kg",
    fields: [{ key: "value", label: "Weight", min: 20, max: 350, decimals: 1 }],
    check: ({ value }) => (value >= 67 && value <= 70 ? "normal" : "watch"),
  },
  temp: {
    label: "Temperature",
    unit: "°C",
    fields: [{ key: "value", label: "Temperature", min: 30, max: 45, decimals: 1 }],
    check: ({ value }) => (value >= 36.1 && value <= 37.2 ? "normal" : "watch"),
  },
  spo2: {
    label: "Blood oxygen",
    unit: "%",
    fields: [{ key: "value", label: "Oxygen saturation", min: 50, max: 100 }],
    check: ({ value }) => (value >= 95 ? "normal" : "watch"),
  },
};

// Order of the choices in the form, most often logged first.
export const VITAL_ORDER = ["bp", "hr", "glucose", "weight", "temp", "spo2"];

// "128/84", "6.2", "72": a reading's value as shown on tiles and in lists.
export function vitalText(id, values) {
  const input = VITAL_INPUTS[id];
  if (input.text) return input.text(values);
  const { decimals } = input.fields[0];
  return decimals ? values.value.toFixed(decimals) : String(values.value);
}

export const vitalTrendValue = (id, values) => (VITAL_INPUTS[id].trend ?? (({ value }) => value))(values);
